from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from .activation import activate, clear_activated
from .governance import analysis_guard_blocked, note_analysis_run, record_external_action
from .models import LaunchPlan
from .report_repair import repair_run_contract
from .registry import load_agents, load_campaign, select_agents, select_campaign
from .runner import (
    DENIED_TOOLS,
    READ_ONLY_TOOLS,
    atomic_json,
    check_run_contract,
    launch_plan,
    launch_post_source_pipeline,
    make_run_id,
    post_source_pipeline_ceiling,
    read_json,
)
from .validation import validate
from .workspace import load_workspace_config, resolve_workspace

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
LARGE_RUN_THRESHOLD = 100
ANALYSIS_EXEMPTIONS = ["calibration", "runtime-debug", "incident-recovery"]


def add_selectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tier",
        action="append",
        default=[],
        choices=["chief", "director", "specialist", "council", "synthesis"],
    )
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--role", action="append", default=[])
    parser.add_argument("--name", action="append", default=[])


def resolve_selection(args: argparse.Namespace, root: Path):
    agents = load_agents(root)
    campaign = load_campaign(args.campaign, root) if getattr(args, "campaign", None) else None
    selected = select_campaign(campaign, agents) if campaign else agents
    if args.tier or args.domain or args.role or args.name:
        selected = select_agents(
            selected,
            tiers=args.tier,
            domains=args.domain,
            roles=args.role,
            names=args.name,
        )
    return selected, campaign


def read_task(args: argparse.Namespace) -> str:
    if getattr(args, "task", None) and getattr(args, "task_file", None):
        raise ValueError("Use either --task or --task-file, not both")
    if getattr(args, "task_file", None):
        task = Path(args.task_file).read_text(encoding="utf-8").strip()
    elif getattr(args, "task", None):
        task = args.task.strip()
    else:
        raise ValueError("A non-empty --task or --task-file is required")
    if not task:
        raise ValueError("A non-empty --task or --task-file is required")
    return task


def plan_dict(
    selected,
    campaign,
    concurrency: int,
    agent_budget: float,
    retries: int,
    workspace: Path,
    *,
    include_post_source: bool = True,
) -> dict:
    source_ceiling = len(selected) * agent_budget * (retries + 1)
    stages = campaign.post_source_pipeline if campaign and include_post_source else ()
    pipeline_ceiling = post_source_pipeline_ceiling(stages)
    ceiling = source_ceiling + pipeline_ceiling
    return {
        "workspace": str(workspace),
        "campaign": campaign.name if campaign else None,
        "agent_count": len(selected),
        "counts_by_tier": dict(Counter(a.tier for a in selected)),
        "counts_by_model": dict(Counter(a.model for a in selected)),
        "concurrency": concurrency,
        "waves": (len(selected) + concurrency - 1) // concurrency if selected else 0,
        "per_agent_budget_usd": agent_budget,
        "retries": retries,
        "source_ceiling_usd": round(source_ceiling, 2),
        "post_source_ceiling_usd": pipeline_ceiling,
        "nominal_configured_ceiling_usd": round(ceiling, 2),
        "post_source_configured": bool(campaign and campaign.post_source_pipeline),
        "post_source_enabled": bool(stages),
        "post_source_pipeline": [
            {
                "name": stage.name,
                "agent": stage.agent_name,
                "model": stage.model,
                "effort": stage.effort,
                "max_turns": stage.max_turns,
                "budget_usd": stage.budget_usd,
                "retries": stage.retries,
            }
            for stage in stages
        ],
        "large_run_ack_required": len(selected) >= LARGE_RUN_THRESHOLD,
        "agents": [a.name for a in selected],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wizengamot",
        description="Operate a privacy-first Claude agent council",
    )
    parser.add_argument("--workspace", type=Path, help="Workspace containing wizengamot.workspace.json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("workspace", help="Show the resolved workspace and configuration")
    sub.add_parser("validate", help="Validate the workspace, agents, context, schema, and campaigns")
    sub.add_parser("count", help="Count agents by tier")

    p_check_run = sub.add_parser(
        "check-run-contract",
        help="Recompute the mechanical report contract for saved run results",
    )
    p_check_run.add_argument("--run-id", required=True)
    p_check_run.add_argument("--name", action="append", default=[])

    p_repair_run = sub.add_parser(
        "repair-run-contract",
        help="Plan or apply deterministic provenance repairs to contract-error final results",
    )
    p_repair_run.add_argument("--run-id", required=True)
    p_repair_run.add_argument(
        "--apply",
        action="store_true",
        help="Back up and replace only reports whose contract error set strictly improves",
    )

    p_record = sub.add_parser(
        "record-action",
        help="Record a completed external action and reset the optional analysis-loop guard",
    )
    p_record.add_argument("--description", required=True)
    p_record.add_argument("--owner", required=True)
    p_record.add_argument("--occurred-at")

    p_list = sub.add_parser("list", help="List agents")
    p_list.add_argument("--campaign")
    add_selectors(p_list)
    p_list.add_argument("--json", action="store_true")

    p_activate = sub.add_parser("activate", help="Copy selected library agents into .claude/agents/activated")
    p_activate.add_argument("--campaign")
    add_selectors(p_activate)
    p_activate.add_argument("--clear", action="store_true")
    p_activate.add_argument("--unsafe-large-activation", action="store_true")

    p_plan = sub.add_parser("plan", help="Preview selection, waves, and nominal configured budget ceiling")
    p_plan.add_argument("--campaign")
    add_selectors(p_plan)
    p_plan.add_argument("--concurrency", type=int)
    p_plan.add_argument("--max-agent-budget", type=float)
    p_plan.add_argument("--retries", type=int, default=0)
    p_plan.add_argument("--no-post-source", action="store_true")
    p_plan.add_argument("--json", action="store_true")

    p_launch = sub.add_parser("launch", help="Dry-run or execute independent Agent SDK sessions")
    p_launch.add_argument("--campaign")
    add_selectors(p_launch)
    p_launch.add_argument("--task")
    p_launch.add_argument("--task-file")
    p_launch.add_argument("--concurrency", type=int)
    p_launch.add_argument("--max-agent-budget", type=float)
    p_launch.add_argument("--max-total-budget", type=float)
    p_launch.add_argument("--retries", type=int, default=0)
    p_launch.add_argument("--run-id")
    p_launch.add_argument("--execute", action="store_true")
    p_launch.add_argument("--ack-large-run", type=int)
    p_launch.add_argument("--unsafe-high-concurrency", action="store_true")
    p_launch.add_argument("--no-skip-existing", action="store_true")
    p_launch.add_argument(
        "--no-post-source",
        action="store_true",
        help="Run only the source roster even when the campaign defines post-source stages",
    )
    p_launch.add_argument(
        "--analysis-exempt",
        choices=ANALYSIS_EXEMPTIONS,
        help="Exclude a calibration/debugging run from the optional analysis-loop counter",
    )

    sub.add_parser(
        "sdk-check",
        help="Import the Agent SDK and construct bounded options without making an API call",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = resolve_workspace(args.workspace)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"Workspace error: {exc}", file=sys.stderr)
        return 2

    if args.command == "record-action":
        record = record_external_action(
            root,
            description=args.description.strip(),
            owner=args.owner.strip(),
            occurred_at=(args.occurred_at.strip() if args.occurred_at else None),
        )
        print(json.dumps(record, indent=2))
        return 0

    if args.command == "workspace":
        config = load_workspace_config(root)
        print(json.dumps({
            "path": str(root),
            "name": config.name,
            "display_name": config.display_name,
            "description": config.description,
            "expected_counts": config.expected_counts,
            "audit_campaign": config.audit_campaign,
            "audit_worker_count": config.audit_worker_count,
            "analysis_loop_guard": {
                "enabled": config.analysis_loop_guard_enabled,
                "max_consecutive_runs": config.analysis_loop_max_consecutive_runs,
            },
        }, indent=2))
        return 0

    if args.command == "validate":
        errors = validate(root)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        config = load_workspace_config(root)
        agents = load_agents(root)
        audit_count = ""
        if config.audit_campaign:
            audit_count = f"; {config.audit_worker_count} {config.audit_campaign} workers"
        print(
            f"Validation passed: {len(agents)} agents; "
            f"{config.default_interactive_count or 0} default interactive definitions{audit_count}."
        )
        return 0

    agents = load_agents(root)
    if args.command == "count":
        print(f"Workspace: {root}")
        print(f"Total: {len(agents)}")
        for tier, count in sorted(Counter(a.tier for a in agents).items()):
            print(f"{tier}: {count}")
        return 0

    if args.command == "check-run-contract":
        if not RUN_ID_RE.fullmatch(args.run_id):
            print(
                "Run ID must start with an alphanumeric character and contain only letters, numbers, "
                "dots, underscores, and hyphens.",
                file=sys.stderr,
            )
            return 2
        try:
            result = check_run_contract(root / "runs" / args.run_id, names=args.name)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0 if result["mechanically_qualified"] else 1

    if args.command == "repair-run-contract":
        if not RUN_ID_RE.fullmatch(args.run_id):
            print(
                "Run ID must start with an alphanumeric character and contain only letters, numbers, "
                "dots, underscores, and hyphens.",
                file=sys.stderr,
            )
            return 2
        try:
            result = repair_run_contract(
                root / "runs" / args.run_id,
                apply=args.apply,
            )
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "sdk-check":
        try:
            from importlib.metadata import version
            from claude_agent_sdk import ClaudeAgentOptions

            profiles = [
                ("fable", "max"),
                ("opus", "xhigh"),
                ("sonnet", "high"),
                ("haiku", "medium"),
            ]
            options = []
            for model, effort in profiles:
                options.append(ClaudeAgentOptions(
                    tools=READ_ONLY_TOOLS,
                    allowed_tools=READ_ONLY_TOOLS,
                    disallowed_tools=DENIED_TOOLS,
                    strict_mcp_config=True,
                    permission_mode="dontAsk",
                    max_turns=1,
                    max_budget_usd=0.01,
                    model=model,
                    output_format={"type": "json_schema", "schema": {"type": "object"}},
                    cwd=root,
                    env={
                        "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1",
                        "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
                        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                        "CLAUDE_CODE_FORK_SUBAGENT": "0",
                        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "1",
                        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
                    },
                    setting_sources=["project"],
                    skills=[],
                    effort=effort,
                ))
            print(
                "Agent SDK static construction passed: "
                f"version {version('claude-agent-sdk')}; {len(options)} model/effort profiles; no API call made."
            )
            return 0
        except Exception as exc:
            print(f"SDK check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    selected, campaign = resolve_selection(args, root)

    if args.command == "list":
        if args.json:
            print(json.dumps([a.__dict__ for a in selected], indent=2, default=list))
        else:
            for agent in selected:
                print(f"{agent.name}\t{agent.tier}\t{agent.domain}\t{agent.role}\t{agent.model}")
        return 0

    if args.command == "activate":
        has_selector = bool(args.campaign or args.tier or args.domain or args.role or args.name)
        if args.clear and not has_selector:
            removed = clear_activated(root)
            print(f"Cleared {removed} activated agent definitions.")
            return 0
        if not has_selector:
            print(
                "Activation requires --campaign, --tier, --domain, --role, or --name. "
                "This prevents accidental activation of the entire library.",
                file=sys.stderr,
            )
            return 2
        if not selected:
            print("No agents selected.", file=sys.stderr)
            return 2
        activatable_count = sum(agent.interactive_path is None for agent in selected)
        if activatable_count > 50 and not args.unsafe_large_activation:
            print(
                f"Activation would copy {activatable_count} definitions into the interactive agent tree. "
                "Use the batch runner for large campaigns or pass --unsafe-large-activation deliberately.",
                file=sys.stderr,
            )
            return 2
        copied = activate(selected, root, clear=args.clear)
        skipped = len(selected) - len(copied)
        print(
            f"Activated {len(copied)} definitions; skipped {skipped} definitions already loaded by default. "
            "Restart Claude Code only when the agents directory did not exist when the session started."
        )
        return 0

    concurrency = (
        args.concurrency
        if args.concurrency is not None
        else (campaign.default_concurrency if campaign else int(os.getenv("WIZENGAMOT_CONCURRENCY", "8")))
    )
    agent_budget = (
        args.max_agent_budget
        if args.max_agent_budget is not None
        else (
            campaign.default_agent_budget_usd
            if campaign
            else float(os.getenv("WIZENGAMOT_AGENT_BUDGET_USD", "0.50"))
        )
    )
    if concurrency < 1 or agent_budget <= 0 or args.retries < 0:
        print("Concurrency and per-agent budget must be positive; retries cannot be negative.", file=sys.stderr)
        return 2
    if getattr(args, "max_total_budget", None) is not None and args.max_total_budget <= 0:
        print("Aggregate budget authorization must be positive.", file=sys.stderr)
        return 2
    if concurrency > 50 and not getattr(args, "unsafe_high_concurrency", False):
        print("Concurrency above 50 requires --unsafe-high-concurrency.", file=sys.stderr)
        return 2
    if not selected:
        print("No agents selected.", file=sys.stderr)
        return 2

    campaign_roster = select_campaign(campaign, agents) if campaign else []
    exact_campaign_roster = (
        bool(campaign)
        and [agent.name for agent in selected] == [agent.name for agent in campaign_roster]
    )
    include_post_source = (
        not getattr(args, "no_post_source", False)
        and exact_campaign_roster
    )
    preview = plan_dict(
        selected,
        campaign,
        concurrency,
        agent_budget,
        args.retries,
        root,
        include_post_source=include_post_source,
    )
    if args.command == "plan":
        if args.json:
            print(json.dumps(preview, indent=2))
        else:
            print(json.dumps({k: v for k, v in preview.items() if k != "agents"}, indent=2))
            print("First agents:")
            for name in preview["agents"][:20]:
                print(f"  {name}")
            if len(preview["agents"]) > 20:
                print(f"  ... {len(preview['agents']) - 20} more")
        return 0

    try:
        if not args.task and not args.task_file and campaign:
            default_task = root / "tasks" / f"{campaign.name}.md"
            if not default_task.exists():
                raise ValueError(f"Default task file is missing: {default_task}")
            task = default_task.read_text(encoding="utf-8").strip()
        else:
            task = read_task(args)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    required_ceiling = float(preview["nominal_configured_ceiling_usd"])
    authorized = args.max_total_budget if args.max_total_budget is not None else required_ceiling
    print(json.dumps({k: v for k, v in preview.items() if k != "agents"}, indent=2))
    print(f"Aggregate authorization supplied: ${authorized:.2f}")
    if not args.execute:
        print("Dry run only. Add --execute to start paid Agent SDK sessions.")
        return 0
    if args.max_total_budget is None:
        print("Execution requires explicit --max-total-budget.", file=sys.stderr)
        return 2
    if authorized + 1e-9 < required_ceiling:
        print(
            f"Aggregate authorization ${authorized:.2f} is below the nominal configured ceiling "
            f"${required_ceiling:.2f}. Reduce agents, retries, or the per-agent value.",
            file=sys.stderr,
        )
        return 2
    if len(selected) >= LARGE_RUN_THRESHOLD and args.ack_large_run != len(selected):
        print(
            f"Execution of {len(selected)} agents requires --ack-large-run {len(selected)}.",
            file=sys.stderr,
        )
        return 2

    run_id = args.run_id or make_run_id(campaign.name if campaign else "adhoc")
    if not RUN_ID_RE.fullmatch(run_id):
        print(
            "Run ID must start with an alphanumeric character and contain only letters, numbers, dots, underscores, and hyphens.",
            file=sys.stderr,
        )
        return 2

    config = load_workspace_config(root)
    if config.analysis_loop_guard_enabled and not args.analysis_exempt:
        blocked, count = analysis_guard_blocked(
            root,
            max_consecutive_runs=config.analysis_loop_max_consecutive_runs,
        )
        if blocked:
            print(
                "Analysis-loop guard blocked this live run: "
                f"{count} consecutive successful analysis runs have occurred since the last recorded external action. "
                "Perform a real-world action and record it with `wizengamot record-action`, or use "
                "`--analysis-exempt calibration|runtime-debug|incident-recovery` only when the run genuinely tests the harness.",
                file=sys.stderr,
            )
            return 2
    elif args.analysis_exempt:
        print(f"Analysis-loop guard exemption: {args.analysis_exempt}")

    run_dir = root / "runs" / run_id
    plan = LaunchPlan(
        agents=tuple(selected),
        task=task,
        concurrency=concurrency,
        per_agent_budget_usd=agent_budget,
        retries=args.retries,
        aggregate_ceiling_usd=authorized,
        run_dir=run_dir,
    )
    async def execute_workflow() -> dict:
        summary = await launch_plan(
            root=root,
            plan=plan,
            campaign_name=campaign.name if campaign else None,
            campaign_prompt=campaign.prompt if campaign else None,
            skip_existing=not args.no_skip_existing,
            emit_progress=True,
        )
        stages = campaign.post_source_pipeline if campaign and include_post_source else ()
        if not stages:
            return summary
        pipeline = await launch_post_source_pipeline(
            root=root,
            source_plan=plan,
            source_summary=summary,
            stages=stages,
            agents=tuple(agents),
            emit_progress=True,
        )
        source_status = summary.get("status")
        summary["source_status"] = source_status
        summary["post_source_pipeline"] = pipeline
        pipeline_cost = float(pipeline.get("estimated_total_cost_usd", 0.0))
        summary["post_source_estimated_cost_usd"] = pipeline_cost
        summary["workflow_estimated_total_cost_usd"] = round(
            float(summary.get("estimated_total_cost_usd", 0.0)) + pipeline_cost,
            6,
        )
        summary["workflow_session_reset_count"] = (
            int(summary.get("session_reset_count", 0))
            + int(pipeline.get("session_reset_count", 0))
        )
        pipeline_status = pipeline.get("status")
        if pipeline_status == "complete":
            summary["status"] = "complete"
        elif pipeline_status == "paused":
            summary["status"] = "paused"
            pipeline_pause = pipeline.get("pause")
            pipeline_pause = pipeline_pause if isinstance(pipeline_pause, dict) else {}
            stage_records = pipeline.get("stages")
            stage_records = stage_records if isinstance(stage_records, list) else []
            active_stage = stage_records[-1] if stage_records else {}
            parent_pause = {
                **pipeline_pause,
                "run_id": plan.run_dir.name,
                "status": "paused",
                "resume_run_id": plan.run_dir.name,
                "pipeline_stage_run_id": active_stage.get("run_id"),
                "pipeline_stage": active_stage.get("name"),
            }
            summary["pause"] = parent_pause
            atomic_json(plan.run_dir / "pause.json", parent_pause)
        elif source_status == "complete" and summary.get("failed", 0) == 0:
            summary["status"] = str(pipeline_status)
        atomic_json(plan.run_dir / "summary.json", summary)
        progress = read_json(plan.run_dir / "progress.json") or {"run_id": plan.run_dir.name}
        progress.update({
            "status": summary["status"],
            "post_source_pipeline_status": pipeline_status,
            "post_source_pipeline_path": str(plan.run_dir / "post-source-pipeline.json"),
            "workflow_session_reset_count": summary["workflow_session_reset_count"],
        })
        atomic_json(plan.run_dir / "progress.json", progress)
        return summary

    try:
        summary = asyncio.run(execute_workflow())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Launch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if summary.get("status") == "paused":
        pause = summary.get("pause")
        pause = pause if isinstance(pause, dict) else {}
        error = pause.get("error", "Claude returned a global provider failure.")
        reset_hint = pause.get("reset_hint", "Resolve the provider failure before resuming.")
        print(f"Run paused: {error}", file=sys.stderr)
        print(str(reset_hint), file=sys.stderr)
        print(f"Resume with the same --run-id {summary['run_id']}.", file=sys.stderr)

    if (
        summary["failed"] == 0
        and summary.get("status") != "paused"
        and config.analysis_loop_guard_enabled
        and not args.analysis_exempt
    ):
        state = note_analysis_run(root)
        print(
            "Analysis-loop state: "
            f"{state['consecutive_analysis_runs']}/{config.analysis_loop_max_consecutive_runs} "
            "successful analysis runs since the last recorded external action."
        )

    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 and summary.get("status") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
