from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentRecord:
    name: str
    title: str
    tier: str
    domain: str
    role: str
    description: str
    model: str
    max_turns: int
    recommended_budget_usd: float
    effort: str
    tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    library_path: str
    interactive_path: str | None
    context: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentRecord":
        raw_context = value.get("context", value.get("knowledge", []))
        return cls(
            name=str(value["name"]),
            title=str(value["title"]),
            tier=str(value["tier"]),
            domain=str(value["domain"]),
            role=str(value["role"]),
            description=str(value["description"]),
            model=str(value["model"]),
            max_turns=int(value["max_turns"]),
            recommended_budget_usd=float(value["recommended_budget_usd"]),
            effort=str(value["effort"]),
            tools=tuple(str(x) for x in value.get("tools", [])),
            disallowed_tools=tuple(str(x) for x in value.get("disallowed_tools", [])),
            library_path=str(value["library_path"]),
            interactive_path=(str(value["interactive_path"]) if value.get("interactive_path") else None),
            context=tuple(str(x) for x in raw_context),
        )


@dataclass(frozen=True)
class Campaign:
    name: str
    description: str
    selectors: dict[str, Any]
    prompt: str
    default_concurrency: int
    default_agent_budget_usd: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Campaign":
        return cls(
            name=str(value["name"]),
            description=str(value["description"]),
            selectors=dict(value["selectors"]),
            prompt=str(value["prompt"]),
            default_concurrency=int(value["default_concurrency"]),
            default_agent_budget_usd=float(value["default_agent_budget_usd"]),
        )


@dataclass(frozen=True)
class WorkspaceConfig:
    name: str
    display_name: str
    description: str
    expected_counts: dict[str, int]
    default_interactive_count: int | None
    audit_campaign: str | None
    audit_worker_count: int | None
    output_schema: str
    instruction_files: tuple[str, ...]
    required_context: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkspaceConfig":
        return cls(
            name=str(value["name"]),
            display_name=str(value.get("display_name", value["name"])),
            description=str(value.get("description", "")),
            expected_counts={str(k): int(v) for k, v in value.get("expected_counts", {}).items()},
            default_interactive_count=(
                int(value["default_interactive_count"])
                if value.get("default_interactive_count") is not None
                else None
            ),
            audit_campaign=(str(value["audit_campaign"]) if value.get("audit_campaign") else None),
            audit_worker_count=(
                int(value["audit_worker_count"])
                if value.get("audit_worker_count") is not None
                else None
            ),
            output_schema=str(value.get("output_schema", "schemas/agent-result.schema.json")),
            instruction_files=tuple(str(x) for x in value.get("instruction_files", ["CLAUDE.md"])),
            required_context=tuple(str(x) for x in value.get("required_context", [])),
        )


@dataclass(frozen=True)
class LaunchPlan:
    agents: tuple[AgentRecord, ...]
    task: str
    concurrency: int
    per_agent_budget_usd: float
    retries: int
    aggregate_ceiling_usd: float
    run_dir: Path
