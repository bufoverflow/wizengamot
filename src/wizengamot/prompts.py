from __future__ import annotations

import json
from pathlib import Path

from .models import AgentRecord
from .workspace import load_workspace_config


def read_agent_body(root: Path, agent: AgentRecord) -> str:
    text = (root / agent.library_path).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Invalid agent frontmatter: {agent.library_path}")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid agent frontmatter: {agent.library_path}")
    return parts[2].strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_system_prompt(root: Path, agent: AgentRecord) -> str:
    config = load_workspace_config(root)
    body = read_agent_body(root, agent)
    context_paths = _dedupe([*config.instruction_files, *config.required_context, *agent.context])
    listed_context = "\n".join(f"- `{path}`" for path in context_paths if (root / path).exists())
    synthesis_instruction = ""
    if agent.tier == "synthesis":
        synthesis_instruction = (
            "\n\nSynthesis output rule: put the complete decision-ready artifact in the optional "
            "`artifact_markdown` field. Record source report paths in `source_report_paths` "
            "and material reconciliation choices in `decision_log`."
        )
    return (
        f"{body}\n\n"
        "## Batch execution boundary\n\n"
        "This invocation is one independent batch session. You cannot spawn agents, run shell "
        "commands, mutate files, or change task state. Perform the assigned analysis yourself. "
        "Record cross-domain work as handoff questions rather than attempting delegation.\n\n"
        "Read the following workspace records before material analysis. Use the smallest relevant "
        "context set, preserve source provenance, and treat missing records as evidence gaps.\n\n"
        f"{listed_context or '- No local context files are configured.'}"
        f"{synthesis_instruction}\n\n"
        f"Batch identity: `{agent.name}`. Set `agent_name` to exactly this value in structured output."
    )


def build_task_prompt(agent: AgentRecord, task: str, campaign_prompt: str | None = None) -> str:
    prefix = f"You are executing as {agent.name} ({agent.title})."
    campaign = f"\n\nCampaign mandate:\n{campaign_prompt.strip()}" if campaign_prompt else ""
    return f"{prefix}{campaign}\n\nAssigned task:\n{task.strip()}\n\nReturn only the required structured report."


def load_output_schema(root: Path) -> dict:
    config = load_workspace_config(root)
    schema = json.loads((root / config.output_schema).read_text(encoding="utf-8"))
    return {"type": "json_schema", "schema": schema}
