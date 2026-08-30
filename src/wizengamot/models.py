from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


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
class PostSourceStage:
    name: str
    agent_name: str
    model: str
    effort: str
    max_turns: int
    budget_usd: float
    retries: int
    task: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PostSourceStage":
        stage = cls(
            name=str(value["name"]),
            agent_name=str(value["agent"]),
            model=str(value["model"]),
            effort=str(value.get("effort", "max")),
            max_turns=int(value.get("max_turns", 36)),
            budget_usd=float(value["budget_usd"]),
            retries=int(value.get("retries", 1)),
            task=str(value["task"]),
        )
        if not STAGE_NAME_RE.fullmatch(stage.name):
            raise ValueError(f"Invalid post-source stage name: {stage.name!r}")
        if not stage.agent_name.strip():
            raise ValueError(f"Post-source stage {stage.name!r} requires an agent")
        if not stage.model.strip():
            raise ValueError(f"Post-source stage {stage.name!r} requires a model")
        if stage.effort not in EFFORT_LEVELS:
            raise ValueError(
                f"Post-source stage {stage.name!r} has invalid effort {stage.effort!r}"
            )
        if stage.max_turns < 1:
            raise ValueError(f"Post-source stage {stage.name!r} max_turns must be positive")
        if stage.budget_usd <= 0:
            raise ValueError(f"Post-source stage {stage.name!r} budget_usd must be positive")
        if stage.retries < 0:
            raise ValueError(f"Post-source stage {stage.name!r} retries cannot be negative")
        if not stage.task.strip():
            raise ValueError(f"Post-source stage {stage.name!r} requires a task")
        return stage


@dataclass(frozen=True)
class Campaign:
    name: str
    description: str
    selectors: dict[str, Any]
    prompt: str
    default_concurrency: int
    default_agent_budget_usd: float
    post_source_pipeline: tuple[PostSourceStage, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Campaign":
        raw_pipeline = value.get("post_source_pipeline", {})
        if not isinstance(raw_pipeline, dict):
            raise ValueError("post_source_pipeline must be an object")
        pipeline = raw_pipeline
        raw_stages = pipeline.get("stages", [])
        if not isinstance(raw_stages, list):
            raise ValueError("post_source_pipeline.stages must be an array")
        return cls(
            name=str(value["name"]),
            description=str(value["description"]),
            selectors=dict(value["selectors"]),
            prompt=str(value["prompt"]),
            default_concurrency=int(value["default_concurrency"]),
            default_agent_budget_usd=float(value["default_agent_budget_usd"]),
            post_source_pipeline=tuple(PostSourceStage.from_dict(stage) for stage in raw_stages),
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
    analysis_loop_guard_enabled: bool
    analysis_loop_max_consecutive_runs: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkspaceConfig":
        raw_guard = value.get("analysis_loop_guard", {})
        guard = raw_guard if isinstance(raw_guard, dict) else {}
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
            analysis_loop_guard_enabled=bool(guard.get("enabled", False)),
            analysis_loop_max_consecutive_runs=max(1, int(guard.get("max_consecutive_runs", 2))),
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
