from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AgentRecord, LaunchPlan
from .prompts import build_system_prompt, build_task_prompt, load_output_schema

READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch", "WebFetch"]
DENIED_TOOLS = ["Agent", "Bash", "Edit", "Write", "NotebookEdit", "TaskCreate", "TaskUpdate", "TaskStop"]
ATTEMPT_RE = re.compile(r"^attempt-(\d+)\.json$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")
        temp = Path(fh.name)
    temp.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def serialize_agent(agent: AgentRecord) -> dict[str, Any]:
    value = asdict(agent)
    for key in ("tools", "disallowed_tools", "context"):
        value[key] = list(value[key])
    return value


def _result_payload(message: Any) -> dict[str, Any]:
    report = getattr(message, "structured_output", None)
    if report is None:
        raw = getattr(message, "result", None)
        if isinstance(raw, str):
            try:
                report = json.loads(raw)
            except json.JSONDecodeError:
                report = {"raw_result": raw}
    return {
        "subtype": getattr(message, "subtype", None),
        "is_error": bool(getattr(message, "is_error", False)),
        "session_id": getattr(message, "session_id", None),
        "num_turns": getattr(message, "num_turns", None),
        "duration_ms": getattr(message, "duration_ms", None),
        "duration_api_ms": getattr(message, "duration_api_ms", None),
        "total_cost_usd": getattr(message, "total_cost_usd", None),
        "stop_reason": getattr(message, "stop_reason", None),
        "terminal_reason": getattr(message, "terminal_reason", None),
        "permission_denials": getattr(message, "permission_denials", None),
        "errors": getattr(message, "errors", None),
        "usage": getattr(message, "usage", None),
        "model_usage": getattr(message, "model_usage", None),
        "report": report,
    }


def payload_succeeded(payload: dict[str, Any] | None, *, expected_agent: str | None = None) -> bool:
    if not payload:
        return False
    result = payload.get("result")
    if not isinstance(result, dict) or bool(result.get("is_error")):
        return False
    report = result.get("report")
    if not isinstance(report, dict):
        return False
    if report.get("status") not in {"complete", "partial", "blocked"}:
        return False
    if expected_agent is not None and report.get("agent_name") != expected_agent:
        return False
    return True


def next_attempt_number(agent_attempts_dir: Path) -> int:
    highest = 0
    if agent_attempts_dir.exists():
        for path in agent_attempts_dir.glob("attempt-*.json"):
            match = ATTEMPT_RE.fullmatch(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def prepare_manifest(
    *,
    path: Path,
    plan: LaunchPlan,
    campaign_name: str | None,
    campaign_prompt: str | None,
) -> None:
    now = utc_now()
    agent_names = [agent.name for agent in plan.agents]
    launch_record = {
        "launched_at": now,
        "concurrency": plan.concurrency,
        "per_agent_budget_usd": plan.per_agent_budget_usd,
        "retries": plan.retries,
        "aggregate_authorization_usd": plan.aggregate_ceiling_usd,
    }
    existing = read_json(path)
    if path.exists() and existing is None:
        raise ValueError(
            f"Run manifest {path} exists but is malformed or unreadable. "
            "Repair the manifest or choose a new --run-id."
        )
    if existing is not None:
        existing_names = [a.get("name") for a in existing.get("agents", []) if isinstance(a, dict)]
        identity_matches = (
            existing.get("campaign") == campaign_name
            and existing.get("task") == plan.task
            and existing_names == agent_names
        )
        if not identity_matches:
            raise ValueError(
                f"Run directory {path.parent} already contains a different campaign, task, or agent selection. "
                "Choose a new --run-id."
            )
        history = existing.setdefault("launch_history", [])
        if not isinstance(history, list):
            raise ValueError(f"Malformed launch history in {path}")
        history.append(launch_record)
        existing["last_launched_at"] = now
        atomic_json(path, existing)
        return

    manifest = {
        "run_id": plan.run_dir.name,
        "campaign": campaign_name,
        "created_at": now,
        "last_launched_at": now,
        "task": plan.task,
        "campaign_prompt": campaign_prompt,
        "agent_count": len(plan.agents),
        "agents": [serialize_agent(agent) for agent in plan.agents],
        "launch_history": [launch_record],
    }
    atomic_json(path, manifest)


async def execute_agent(
    *,
    root: Path,
    agent: AgentRecord,
    task: str,
    campaign_prompt: str | None,
    output_path: Path,
    max_budget_usd: float,
    max_turns: int | None,
    attempt: int,
) -> dict[str, Any]:
    started = utc_now()
    start_clock = time.monotonic()
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    except ImportError as exc:
        raise RuntimeError("claude-agent-sdk is not installed. Run ./scripts/bootstrap.sh") from exc

    options = ClaudeAgentOptions(
        tools=READ_ONLY_TOOLS,
        allowed_tools=READ_ONLY_TOOLS,
        system_prompt=build_system_prompt(root, agent),
        strict_mcp_config=True,
        permission_mode="dontAsk",
        max_turns=max_turns or agent.max_turns,
        max_budget_usd=max_budget_usd,
        disallowed_tools=DENIED_TOOLS,
        model=agent.model,
        output_format=load_output_schema(root),
        cwd=root,
        env={
            "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1",
            "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_FORK_SUBAGENT": "0",
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "1",
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
            "CLAUDE_CODE_MAX_RETRIES": "2",
            "API_TIMEOUT_MS": "600000",
        },
        setting_sources=["project"],
        skills=[],
        effort=agent.effort,
    )

    final: Any = None
    prompt = build_task_prompt(agent, task, campaign_prompt)
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                final = message
        if final is None:
            raise RuntimeError("Agent session ended without a ResultMessage")
        payload = {
            "agent": serialize_agent(agent),
            "attempt": attempt,
            "started_at": started,
            "completed_at": utc_now(),
            "wall_seconds": round(time.monotonic() - start_clock, 3),
            "result": _result_payload(final),
        }
    except Exception as exc:
        payload = {
            "agent": serialize_agent(agent),
            "attempt": attempt,
            "started_at": started,
            "completed_at": utc_now(),
            "wall_seconds": round(time.monotonic() - start_clock, 3),
            "result": {
                "subtype": "runner_exception",
                "is_error": True,
                "total_cost_usd": None,
                "errors": [f"{type(exc).__name__}: {exc}"],
                "report": None,
            },
        }
    atomic_json(output_path, payload)
    return payload


async def launch_plan(
    *,
    root: Path,
    plan: LaunchPlan,
    campaign_name: str | None,
    campaign_prompt: str | None,
    skip_existing: bool = True,
    emit_progress: bool = False,
) -> dict[str, Any]:
    plan.run_dir.mkdir(parents=True, exist_ok=True)
    results_dir = plan.run_dir / "results"
    attempts_root = plan.run_dir / "attempts"
    results_dir.mkdir(parents=True, exist_ok=True)
    attempts_root.mkdir(parents=True, exist_ok=True)
    prepare_manifest(
        path=plan.run_dir / "manifest.json",
        plan=plan,
        campaign_name=campaign_name,
        campaign_prompt=campaign_prompt,
    )

    progress_path = plan.run_dir / "progress.json"
    progress_lock = asyncio.Lock()
    progress: dict[str, Any] = {
        "run_id": plan.run_dir.name,
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "total": len(plan.agents),
        "completed": 0,
        "valid_reports": 0,
        "failed": 0,
        "skipped_successful": 0,
        "report_status_counts": {},
        "last_agent": None,
    }
    atomic_json(progress_path, progress)

    async def record_progress(agent: AgentRecord, payload: dict[str, Any], was_skipped: bool) -> None:
        result = payload.get("result") if isinstance(payload, dict) else None
        report = result.get("report") if isinstance(result, dict) else None
        report_status = report.get("status") if isinstance(report, dict) else "runner-error"
        if not isinstance(report_status, str):
            report_status = "malformed-report"
        valid = payload_succeeded(payload, expected_agent=agent.name)
        async with progress_lock:
            progress["completed"] += 1
            progress["valid_reports"] += int(valid)
            progress["failed"] += int(not valid)
            progress["skipped_successful"] += int(was_skipped)
            counts = progress["report_status_counts"]
            counts[report_status] = counts.get(report_status, 0) + 1
            progress["last_agent"] = agent.name
            progress["updated_at"] = utc_now()
            atomic_json(progress_path, progress)
            if emit_progress:
                suffix = " skipped" if was_skipped else ""
                print(
                    f"[{progress['completed']:03d}/{progress['total']:03d}] "
                    f"{report_status:<12} {agent.name}{suffix}",
                    flush=True,
                )

    semaphore = asyncio.Semaphore(plan.concurrency)

    async def worker(agent: AgentRecord) -> tuple[dict[str, Any], bool]:
        final_path = results_dir / f"{agent.name}.json"
        existing = read_json(final_path)
        if skip_existing and payload_succeeded(existing, expected_agent=agent.name):
            await record_progress(agent, existing, True)
            return existing, True

        agent_attempts_dir = attempts_root / agent.name
        first_attempt = next_attempt_number(agent_attempts_dir)
        last: dict[str, Any] | None = None
        async with semaphore:
            for attempt in range(first_attempt, first_attempt + plan.retries + 1):
                attempt_path = agent_attempts_dir / f"attempt-{attempt}.json"
                last = await execute_agent(
                    root=root,
                    agent=agent,
                    task=plan.task,
                    campaign_prompt=campaign_prompt,
                    output_path=attempt_path,
                    max_budget_usd=plan.per_agent_budget_usd,
                    max_turns=None,
                    attempt=attempt,
                )
                atomic_json(final_path, last)
                if payload_succeeded(last, expected_agent=agent.name):
                    break
        assert last is not None
        await record_progress(agent, last, False)
        return last, False

    outcomes = await asyncio.gather(*(worker(agent) for agent in plan.agents))
    outputs = [payload for payload, _ in outcomes]
    skipped = sum(was_skipped for _, was_skipped in outcomes)

    attempt_payloads = [
        payload
        for path in attempts_root.rglob("attempt-*.json")
        if (payload := read_json(path)) is not None
    ]
    attempt_costs = [payload.get("result", {}).get("total_cost_usd") for payload in attempt_payloads]
    known_costs = [float(cost) for cost in attempt_costs if isinstance(cost, (int, float))]
    succeeded = sum(
        payload_succeeded(payload, expected_agent=agent.name)
        for payload, agent in zip(outputs, plan.agents, strict=True)
    )
    report_status_counts: dict[str, int] = {}
    for payload in outputs:
        result = payload.get("result") if isinstance(payload, dict) else None
        report = result.get("report") if isinstance(result, dict) else None
        status = report.get("status") if isinstance(report, dict) else "runner-error"
        if not isinstance(status, str):
            status = "malformed-report"
        report_status_counts[status] = report_status_counts.get(status, 0) + 1
    summary = {
        "run_id": plan.run_dir.name,
        "completed_at": utc_now(),
        "agent_count": len(outputs),
        "succeeded": succeeded,
        "failed": len(outputs) - succeeded,
        "skipped_successful": skipped,
        "report_status_counts": report_status_counts,
        "attempt_count": len(attempt_payloads),
        "known_cost_attempts": len(known_costs),
        "unknown_cost_attempts": len(attempt_payloads) - len(known_costs),
        "estimated_total_cost_usd": round(sum(known_costs), 6),
        "results_dir": str(results_dir),
        "attempts_dir": str(attempts_root),
    }
    progress["completed_at"] = summary["completed_at"]
    progress["updated_at"] = summary["completed_at"]
    progress["report_status_counts"] = report_status_counts
    atomic_json(progress_path, progress)
    atomic_json(plan.run_dir / "summary.json", summary)
    return summary
