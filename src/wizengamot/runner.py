from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .live import LiveTail, describe_sdk_message
from .models import AgentRecord, LaunchPlan
from .prompts import build_system_prompt, build_task_prompt, load_output_schema
from .report_contract import annotate_report_contract, payload_contract_errors

READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch", "WebFetch"]
DENIED_TOOLS = ["Agent", "Bash", "Edit", "Write", "NotebookEdit", "TaskCreate", "TaskUpdate", "TaskStop"]
ATTEMPT_RE = re.compile(r"^attempt-(\d+)\.json$")
RESULT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SESSION_LIMIT_PATTERNS = (
    re.compile(r"\bsession(?:[\s_-]+)limit\b", re.IGNORECASE),
    re.compile(
        r"\b(?:claude(?: code)?|usage|message)[\s_-]+limit\b.*"
        r"\b(?:hit|reached|exceeded|exhausted|reset|resets)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:hit|reached)\s+(?:your\s+)?(?:claude(?: code)?\s+)?(?:usage\s+)?limit\b",
        re.IGNORECASE,
    ),
)
ACCOUNT_QUOTA_PATTERNS = (
    re.compile(r"\binsufficient[_ -]quota\b", re.IGNORECASE),
    re.compile(r"\bquota(?:[_ -]+exceeded|[_ -]+exhausted)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:account|organization|org|monthly|spending|billing|usage)\b.*"
        r"\b(?:quota|credits?|limit)\b.*\b(?:exceeded|reached|exhausted|depleted)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcredit balance is too low\b", re.IGNORECASE),
)
AUTHENTICATION_PATTERNS = (
    re.compile(r"\bauthentication[_ -]+error\b", re.IGNORECASE),
    re.compile(r"\b(?:failed|unable) to authenticate\b", re.IGNORECASE),
    re.compile(r"\binvalid (?:x-)?api[_ -]?key\b", re.IGNORECASE),
    re.compile(r"\b401\b.*\b(?:unauthorized|authentication|credentials?)\b", re.IGNORECASE),
    re.compile(r"\bnot logged in\b", re.IGNORECASE),
    re.compile(r"\bplease (?:run\s+)?/?login\b", re.IGNORECASE),
    re.compile(r"\b(?:oauth\s+)?token\b.*\b(?:expired|invalid|revoked)\b", re.IGNORECASE),
    re.compile(r"\bcredentials?\b.*\b(?:missing|expired|invalid|revoked)\b", re.IGNORECASE),
)
RESET_HINT_RE = re.compile(
    r"\b(?:limit\s+)?resets?\b[^.\n]*|\btry again (?:at|after|in|on)\b[^.\n]*",
    re.IGNORECASE,
)
RELATIVE_RESET_RE = re.compile(
    r"\b(?:resets?|retry|try again)\b[^.\n]*?\b(?:in|after)\s+"
    r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)
ISO_RESET_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)\b",
    re.IGNORECASE,
)
CLOCK_RESET_RE = re.compile(
    r"\b(?:resets?|reset|try again)\b[^.\n]*?\b(?:at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b"
    r"(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)
SESSION_RESET_FALLBACK_SECONDS = 300.0
SESSION_RESET_GRACE_SECONDS = 5.0
SESSION_RESET_MAX_SLEEP_SECONDS = 21600.0


@dataclass(frozen=True)
class WorkerOutcome:
    agent: AgentRecord
    payload: dict[str, Any] | None
    state: str
    was_skipped: bool = False


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


def _error_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_error_strings(item))
        return strings
    if isinstance(value, (list, tuple, set)):
        strings = []
        for item in value:
            strings.extend(_error_strings(item))
        return strings
    return []


def classify_global_provider_failure(payload: dict[str, Any] | None) -> dict[str, str] | None:
    """Return a campaign-wide Claude failure when retrying locally cannot help.

    Ordinary transport errors and provider rate limits deliberately do not trip
    this circuit breaker. Those may be isolated to one session and retain the
    configured retry behavior.
    """

    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict) or not bool(result.get("is_error")):
        return None

    values: list[str] = []
    for key in (
        "errors",
        "error",
        "error_message",
        "message",
        "detail",
        "subtype",
        "terminal_reason",
        "stop_reason",
    ):
        values.extend(_error_strings(result.get(key)))
    if not values:
        return None

    categories = (
        ("account-quota", ACCOUNT_QUOTA_PATTERNS),
        ("session-limit", SESSION_LIMIT_PATTERNS),
        ("authentication", AUTHENTICATION_PATTERNS),
    )
    matched_code: str | None = None
    matched_error: str | None = None
    for code, patterns in categories:
        for value in values:
            if any(pattern.search(value) for pattern in patterns):
                matched_code = code
                matched_error = value
                break
        if matched_code is not None:
            break
    if matched_code is None or matched_error is None:
        return None

    combined = " ".join(values)
    reset_match = RESET_HINT_RE.search(combined)
    if reset_match is not None:
        reset_hint = reset_match.group(0).strip()
    elif matched_code == "session-limit":
        reset_hint = "Resume after the Claude session limit resets."
    elif matched_code == "account-quota":
        reset_hint = "Restore or increase the Claude account quota before resuming."
    else:
        reset_hint = "Restore Claude authentication before resuming."

    return {
        "provider": "claude",
        "code": matched_code,
        "error": matched_error,
        "reset_hint": reset_hint,
    }


def session_limit_reset_at(
    failure: dict[str, Any],
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Resolve a provider reset hint to an aware timestamp when possible."""

    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    text = " ".join(
        value for value in (failure.get("error"), failure.get("reset_hint"))
        if isinstance(value, str) and value.strip()
    )

    relative = RELATIVE_RESET_RE.search(text)
    if relative is not None:
        amount = float(relative.group(1))
        unit = relative.group(2).lower()
        if unit.startswith(("hour", "hr")):
            seconds = amount * 3600
        elif unit.startswith(("minute", "min")):
            seconds = amount * 60
        else:
            seconds = amount
        return current + timedelta(seconds=min(seconds, SESSION_RESET_MAX_SLEEP_SECONDS))

    absolute = ISO_RESET_RE.search(text)
    if absolute is not None:
        raw = absolute.group(1).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=current.tzinfo)

    clock = CLOCK_RESET_RE.search(text)
    if clock is None:
        return None
    raw_hour = int(clock.group(1))
    minute = int(clock.group(2) or 0)
    if not 1 <= raw_hour <= 12 or not 0 <= minute <= 59:
        return None
    hour = raw_hour % 12
    if clock.group(3).lower() == "pm":
        hour += 12
    timezone_name = clock.group(4)
    target_timezone = current.astimezone().tzinfo or timezone.utc
    if timezone_name:
        try:
            target_timezone = ZoneInfo(timezone_name.strip())
        except (ValueError, ZoneInfoNotFoundError):
            pass
    local_now = current.astimezone(target_timezone)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        if local_now - candidate <= timedelta(minutes=5):
            candidate = local_now
        else:
            candidate += timedelta(days=1)
    return candidate


def session_limit_wait_seconds(
    failure: dict[str, Any],
    *,
    now: datetime | None = None,
) -> float:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    reset_at = session_limit_reset_at(failure, now=current)
    if reset_at is None:
        return SESSION_RESET_FALLBACK_SECONDS
    delay = (reset_at.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds()
    return min(
        SESSION_RESET_MAX_SLEEP_SECONDS,
        max(SESSION_RESET_GRACE_SECONDS, delay + SESSION_RESET_GRACE_SECONDS),
    )


async def wait_for_session_reset(seconds: float) -> None:
    await asyncio.sleep(seconds)


def record_session_wait(
    run_dir: Path,
    summary: dict[str, Any],
    failure: dict[str, Any],
    wait_seconds: float,
) -> dict[str, Any]:
    """Persist a durable checkpoint before yielding to the provider reset window."""

    waits_path = run_dir / "session-waits.json"
    saved = read_json(waits_path) or {"run_id": run_dir.name, "waits": []}
    waits = saved.get("waits")
    if not isinstance(waits, list):
        waits = []
        saved["waits"] = waits
    started = datetime.now(timezone.utc)
    record = {
        "wait_number": len(waits) + 1,
        "started_at": started.isoformat(),
        "resume_at": (started + timedelta(seconds=wait_seconds)).isoformat(),
        "wait_seconds": round(wait_seconds, 3),
        "agent_name": failure.get("agent_name"),
        "attempt": failure.get("attempt"),
        "error": failure.get("error"),
        "reset_hint": failure.get("reset_hint"),
    }
    waits.append(record)
    atomic_json(waits_path, saved)

    waiting_pause = {**failure, **record, "status": "waiting-for-session-reset"}
    summary.update({
        "status": "waiting-for-session-reset",
        "session_limit_wait_count": len(waits),
        "session_wait": record,
        "pause": waiting_pause,
    })
    atomic_json(run_dir / "summary.json", summary)

    progress = read_json(run_dir / "progress.json") or {"run_id": run_dir.name}
    progress.update({
        "status": "waiting-for-session-reset",
        "updated_at": started.isoformat(),
        "session_limit_wait_count": len(waits),
        "session_wait": record,
        "pause": waiting_pause,
    })
    atomic_json(run_dir / "progress.json", progress)

    pause = read_json(run_dir / "pause.json") or {"run_id": run_dir.name}
    pause.update(waiting_pause)
    pause["resume_run_id"] = run_dir.name
    atomic_json(run_dir / "pause.json", pause)
    return record


def _result_payload(message: Any) -> dict[str, Any]:
    report = getattr(message, "structured_output", None)
    raw = getattr(message, "result", None)
    is_error = bool(getattr(message, "is_error", False))
    if report is None:
        if isinstance(raw, str):
            try:
                report = json.loads(raw)
            except json.JSONDecodeError:
                report = {"raw_result": raw}
    return {
        "subtype": getattr(message, "subtype", None),
        "is_error": is_error,
        "error_message": raw if is_error and isinstance(raw, str) else None,
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
    if payload_contract_errors(payload):
        return False
    return True


def payload_report_status(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "runner-error"
    result = payload.get("result")
    report = result.get("report") if isinstance(result, dict) else None
    if not isinstance(report, dict):
        return "runner-error"
    status = report.get("status")
    if not isinstance(status, str):
        return "malformed-report"
    if payload_contract_errors(payload):
        return "contract-error"
    return status


def check_run_contract(run_dir: Path, *, names: list[str] | None = None) -> dict[str, Any]:
    results_dir = run_dir / "results"
    selected = names or []
    invalid_names = [name for name in selected if not RESULT_NAME_RE.fullmatch(name)]
    if invalid_names:
        raise ValueError(f"Invalid result agent name: {invalid_names[0]}")
    paths = (
        [results_dir / f"{name}.json" for name in selected]
        if selected
        else sorted(results_dir.glob("*.json"))
    )
    if not paths:
        raise ValueError(f"Run {run_dir.name} has no result payloads")

    reports: list[dict[str, Any]] = []
    for path in paths:
        payload = read_json(path)
        expected_agent = path.stem
        exists = path.is_file()
        errors = payload_contract_errors(payload) if isinstance(payload, dict) else []
        mechanically_qualified = payload_succeeded(payload, expected_agent=expected_agent)
        reports.append({
            "agent_name": expected_agent,
            "result_path": str(path),
            "exists": exists,
            "status": payload_report_status(payload),
            "report_contract_errors": errors,
            "mechanically_qualified": mechanically_qualified,
        })

    return {
        "run_id": run_dir.name,
        "report_count": len(reports),
        "mechanically_qualified": all(report["mechanically_qualified"] for report in reports),
        "semantic_review_required": True,
        "reports": reports,
    }


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
    activity_callback: Callable[[str], None] | None = None,
    prior_report_contract_errors: list[str] | None = None,
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
    if prior_report_contract_errors:
        violations = "\n".join(f"- {error}" for error in prior_report_contract_errors)
        prompt = (
            f"{prompt}\n\n"
            "The previous attempt was rejected by the runtime report-contract validator. "
            "Repair every provenance violation below in this new report:\n"
            f"{violations}"
        )
    try:
        async for message in query(prompt=prompt, options=options):
            activity = describe_sdk_message(message)
            if activity_callback is not None and activity:
                activity_callback(activity)
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
    annotate_report_contract(payload)
    atomic_json(output_path, payload)
    return payload


async def _launch_plan_pass(
    *,
    root: Path,
    plan: LaunchPlan,
    campaign_name: str | None,
    campaign_prompt: str | None,
    skip_existing: bool = True,
    emit_progress: bool = False,
    record_launch: bool,
    eligible_agent_names: set[str] | None,
    remaining_attempts: dict[str, int],
) -> dict[str, Any]:
    plan.run_dir.mkdir(parents=True, exist_ok=True)
    results_dir = plan.run_dir / "results"
    attempts_root = plan.run_dir / "attempts"
    results_dir.mkdir(parents=True, exist_ok=True)
    attempts_root.mkdir(parents=True, exist_ok=True)
    if record_launch:
        prepare_manifest(
            path=plan.run_dir / "manifest.json",
            plan=plan,
            campaign_name=campaign_name,
            campaign_prompt=campaign_prompt,
        )

    progress_path = plan.run_dir / "progress.json"
    pause_path = plan.run_dir / "pause.json"
    previous_pause = read_json(pause_path)
    progress_lock = asyncio.Lock()
    progress: dict[str, Any] = {
        "run_id": plan.run_dir.name,
        "status": "running",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "total": len(plan.agents),
        "completed": 0,
        "valid_reports": 0,
        "failed": 0,
        "deferred": 0,
        "skipped_successful": 0,
        "completed_agents": [],
        "failed_agents": [],
        "deferred_agents": [],
        "report_status_counts": {},
        "last_agent": None,
    }
    atomic_json(progress_path, progress)
    live = LiveTail(total=len(plan.agents), enabled=emit_progress)
    progress_lines: list[str] = []
    await live.start()

    async def record_progress(agent: AgentRecord, payload: dict[str, Any], was_skipped: bool) -> None:
        report_status = payload_report_status(payload)
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
            live.complete_agent(agent.name)
            if emit_progress:
                suffix = " skipped" if was_skipped else ""
                line = (
                    f"[{progress['completed']:03d}/{progress['total']:03d}] "
                    f"{report_status:<12} {agent.name}{suffix}"
                )
                if live.enabled:
                    progress_lines.append(line)
                else:
                    print(line, flush=True)

    semaphore = asyncio.Semaphore(plan.concurrency)
    pause_event = asyncio.Event()
    pause_failure: dict[str, Any] | None = None

    async def trip_global_failure(
        agent: AgentRecord,
        payload: dict[str, Any],
        failure: dict[str, str],
    ) -> None:
        nonlocal pause_failure
        async with progress_lock:
            if pause_failure is not None:
                return
            pause_failure = {
                **failure,
                "agent_name": agent.name,
                "attempt": payload.get("attempt"),
                "paused_at": utc_now(),
            }
            pause_event.set()
            progress["status"] = "pausing"
            progress["pause"] = dict(pause_failure)
            progress["updated_at"] = pause_failure["paused_at"]
            atomic_json(progress_path, progress)
            atomic_json(pause_path, {
                "run_id": plan.run_dir.name,
                "status": "paused",
                **pause_failure,
                "resume_run_id": plan.run_dir.name,
            })

    async def worker(agent: AgentRecord) -> WorkerOutcome:
        final_path = results_dir / f"{agent.name}.json"
        existing = read_json(final_path)
        if skip_existing and payload_succeeded(existing, expected_agent=agent.name):
            await record_progress(agent, existing, True)
            return WorkerOutcome(agent=agent, payload=existing, state="completed", was_skipped=True)
        if eligible_agent_names is not None and agent.name not in eligible_agent_names:
            if existing is None:
                return WorkerOutcome(agent=agent, payload=None, state="deferred")
            await record_progress(agent, existing, False)
            return WorkerOutcome(agent=agent, payload=existing, state="failed")

        agent_attempts_dir = attempts_root / agent.name
        first_attempt = next_attempt_number(agent_attempts_dir)
        last: dict[str, Any] | None = None
        last_was_global = False
        saved_contract_errors = payload_contract_errors(existing) if isinstance(existing, dict) else []
        prior_report_contract_errors: list[str] | None = saved_contract_errors or None

        def on_activity(activity: str) -> None:
            live.update_agent(agent.name, activity)

        async with semaphore:
            if pause_event.is_set():
                return WorkerOutcome(agent=agent, payload=None, state="deferred")
            live.start_agent(agent.name, f"{agent.model} · attempt {first_attempt}")
            available_attempts = remaining_attempts.get(agent.name, 0)
            for attempt in range(first_attempt, first_attempt + available_attempts):
                if pause_event.is_set():
                    break
                attempt_path = agent_attempts_dir / f"attempt-{attempt}.json"
                live.update_agent(agent.name, f"{agent.model} · attempt {attempt} starting")
                last = await execute_agent(
                    root=root,
                    agent=agent,
                    task=plan.task,
                    campaign_prompt=campaign_prompt,
                    output_path=attempt_path,
                    max_budget_usd=plan.per_agent_budget_usd,
                    max_turns=None,
                    attempt=attempt,
                    activity_callback=on_activity,
                    prior_report_contract_errors=prior_report_contract_errors,
                )
                contract_errors = annotate_report_contract(last)
                atomic_json(attempt_path, last)
                atomic_json(final_path, last)
                global_failure = classify_global_provider_failure(last)
                if global_failure is not None:
                    last_was_global = True
                    await trip_global_failure(agent, last, global_failure)
                    break
                remaining_attempts[agent.name] = max(0, remaining_attempts[agent.name] - 1)
                if payload_succeeded(last, expected_agent=agent.name):
                    break
                prior_report_contract_errors = contract_errors or None
                # Give other completed sessions a scheduling point to trip the
                # global breaker before this worker begins another attempt.
                await asyncio.sleep(0)
        if last is None:
            return WorkerOutcome(agent=agent, payload=None, state="deferred")
        await record_progress(agent, last, False)
        if payload_succeeded(last, expected_agent=agent.name):
            state = "completed"
        elif pause_event.is_set() and not last_was_global and remaining_attempts[agent.name] > 0:
            state = "deferred"
        else:
            state = "failed"
        return WorkerOutcome(agent=agent, payload=last, state=state)

    try:
        outcomes = await asyncio.gather(*(worker(agent) for agent in plan.agents))
    finally:
        await live.stop()
        if emit_progress and live.enabled:
            for line in progress_lines:
                print(line, flush=True)

    outputs = [outcome.payload for outcome in outcomes if outcome.payload is not None]
    skipped = sum(outcome.was_skipped for outcome in outcomes)
    completed_agents = [outcome.agent.name for outcome in outcomes if outcome.state == "completed"]
    failed_agents = [outcome.agent.name for outcome in outcomes if outcome.state == "failed"]
    deferred_agents = [outcome.agent.name for outcome in outcomes if outcome.state == "deferred"]

    attempt_payloads = [
        payload
        for path in attempts_root.rglob("attempt-*.json")
        if (payload := read_json(path)) is not None
    ]
    attempt_costs = [payload.get("result", {}).get("total_cost_usd") for payload in attempt_payloads]
    known_costs = [float(cost) for cost in attempt_costs if isinstance(cost, (int, float))]
    succeeded = len(completed_agents)
    report_status_counts: dict[str, int] = {}
    for payload in outputs:
        status = payload_report_status(payload)
        report_status_counts[status] = report_status_counts.get(status, 0) + 1
    finalized_at = utc_now()
    paused = pause_failure is not None
    summary = {
        "run_id": plan.run_dir.name,
        "status": "paused" if paused else "complete",
        "completed_at": None if paused else finalized_at,
        "paused_at": pause_failure["paused_at"] if pause_failure is not None else None,
        "agent_count": len(plan.agents),
        "completed": len(outputs),
        "succeeded": succeeded,
        "failed": len(failed_agents),
        "deferred": len(deferred_agents),
        "completed_agents": completed_agents,
        "failed_agents": failed_agents,
        "deferred_agents": deferred_agents,
        "skipped_successful": skipped,
        "report_status_counts": report_status_counts,
        "attempt_count": len(attempt_payloads),
        "known_cost_attempts": len(known_costs),
        "unknown_cost_attempts": len(attempt_payloads) - len(known_costs),
        "estimated_total_cost_usd": round(sum(known_costs), 6),
        "results_dir": str(results_dir),
        "attempts_dir": str(attempts_root),
        "pause": dict(pause_failure) if pause_failure is not None else None,
    }
    progress.update({
        "status": summary["status"],
        "completed_at": summary["completed_at"],
        "paused_at": summary["paused_at"],
        "updated_at": finalized_at,
        "completed": len(outputs),
        "valid_reports": succeeded,
        "failed": len(failed_agents),
        "deferred": len(deferred_agents),
        "completed_agents": completed_agents,
        "failed_agents": failed_agents,
        "deferred_agents": deferred_agents,
        "report_status_counts": report_status_counts,
        "pause": dict(pause_failure) if pause_failure is not None else None,
    })
    atomic_json(progress_path, progress)
    atomic_json(plan.run_dir / "summary.json", summary)
    if pause_failure is not None:
        atomic_json(pause_path, {
            "run_id": plan.run_dir.name,
            "status": "paused",
            **pause_failure,
            "resume_run_id": plan.run_dir.name,
            "completed_agents": completed_agents,
            "failed_agents": failed_agents,
            "deferred_agents": deferred_agents,
            "summary_path": str(plan.run_dir / "summary.json"),
        })
    elif previous_pause is not None and previous_pause.get("status") in {
        "paused",
        "waiting-for-session-reset",
    }:
        previous_pause["status"] = "resumed"
        previous_pause["resumed_at"] = finalized_at
        atomic_json(pause_path, previous_pause)
    return summary


async def launch_plan(
    *,
    root: Path,
    plan: LaunchPlan,
    campaign_name: str | None,
    campaign_prompt: str | None,
    skip_existing: bool = True,
    emit_progress: bool = False,
) -> dict[str, Any]:
    """Run a plan once from the user's perspective, across session reset windows."""

    first_pass = True
    eligible_agent_names: set[str] | None = None
    remaining_attempts = {
        agent.name: plan.retries + 1
        for agent in plan.agents
    }
    while True:
        summary = await _launch_plan_pass(
            root=root,
            plan=plan,
            campaign_name=campaign_name,
            campaign_prompt=campaign_prompt,
            # Internal recovery passes must preserve every successful result,
            # including when the original user launch disabled resume skipping.
            skip_existing=skip_existing if first_pass else True,
            emit_progress=emit_progress,
            record_launch=first_pass,
            eligible_agent_names=eligible_agent_names,
            remaining_attempts=remaining_attempts,
        )
        first_pass = False

        pause = summary.get("pause")
        failure = pause if isinstance(pause, dict) else {}
        if summary.get("status") != "paused" or failure.get("code") != "session-limit":
            saved_waits = read_json(plan.run_dir / "session-waits.json")
            waits = saved_waits.get("waits") if isinstance(saved_waits, dict) else None
            if isinstance(waits, list) and waits:
                summary["session_limit_wait_count"] = len(waits)
                summary["session_waits_path"] = str(plan.run_dir / "session-waits.json")
                atomic_json(plan.run_dir / "summary.json", summary)
                progress = read_json(plan.run_dir / "progress.json") or {"run_id": plan.run_dir.name}
                progress["session_limit_wait_count"] = len(waits)
                progress["session_waits_path"] = str(plan.run_dir / "session-waits.json")
                atomic_json(plan.run_dir / "progress.json", progress)
            return summary

        wait_seconds = session_limit_wait_seconds(failure)
        wait_record = record_session_wait(plan.run_dir, summary, failure, wait_seconds)
        eligible_agent_names = set(summary.get("deferred_agents", []))
        source_agent = failure.get("agent_name")
        if isinstance(source_agent, str):
            eligible_agent_names.add(source_agent)
        if emit_progress:
            print(
                "Claude session limit reached; run checkpointed. "
                f"Waiting until {wait_record['resume_at']} before resuming automatically.",
                file=sys.stderr,
                flush=True,
            )
        await wait_for_session_reset(wait_seconds)
        if emit_progress:
            print(
                f"Claude reset wait complete; resuming run {plan.run_dir.name}.",
                file=sys.stderr,
                flush=True,
            )
