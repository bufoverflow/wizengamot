from __future__ import annotations

import json
from pathlib import Path

from .models import AgentRecord
from .workspace import load_workspace_config


EPISTEMIC_RULES = """## Epistemic governance

This report is model-generated reasoning. It is not empirical evidence and must never be treated as an independent validation event. Source evidence carried into the report retains its original provenance.

For every material finding:
- classify the evidence source as customer evidence, project record, external primary, external secondary, model reasoning, or unknown;
- classify novelty as retrieved, derived, corroborated, contradicted, novel, or unresolved;
- cite source identifiers when available;
- do not count rediscovery of an existing project observation as independent corroboration;
- do not convert model agreement, model confidence, or failure to falsify into customer validation, willingness-to-pay, observed demand, observed workflow, traction, or other empirical scores.

When relevant internal evidence exists, reconcile the conclusion against it. Falsification and council work should cite relevant interview/customer records rather than attacking only a clean-room restatement of the thesis. Missing relevant source evidence is an evidence gap.

Every complete or partial report should terminate in at least one concrete next action with an owner and target date. Prefer external evidence-generating actions when uncertainty can be resolved in the world. Match the evidence bar to reversibility: calls, emails, interviews, and small pilots usually deserve a near-zero or low bar; expensive or difficult-to-reverse commitments may justify a higher bar.

Review whether the evidence threshold for action is drifting upward. A higher bar is justified by new external evidence, a customer-raised question, or a real operational constraint. A hypothetical objection generated only by the model does not, by itself, justify delaying a cheap reversible action.
"""


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
            "\n\nSynthesis output rule: put a decision-ready artifact in the optional `artifact_markdown` "
            "field only when the task explicitly calls for an artifact. Preserve human authorship for high-stakes "
            "founder narrative by default: audit claims, evidence, ambiguity, confidentiality, and objections rather "
            "than silently replacing the author's voice. Record source report paths in `source_report_paths` and "
            "material reconciliation choices in `decision_log`."
        )
    return (
        f"{body}\n\n"
        "## Batch execution boundary\n\n"
        "This invocation is one independent batch session. You cannot spawn agents, run shell "
        "commands, mutate files, or change task state. Perform the assigned analysis yourself. "
        "Record cross-domain work as handoff questions rather than attempting delegation.\n\n"
        f"{EPISTEMIC_RULES}\n"
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

    # Claude Code's structured-output validator currently rejects an explicit
    # JSON Schema Draft 2020-12 dialect marker. Keep the canonical schema file
    # standards-compliant and normalize only the in-memory copy sent to Claude.
    schema.pop("$schema", None)

    return {"type": "json_schema", "schema": schema}
