from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import AgentRecord


RECOVERY_OVERRIDES_FILENAME = "recovery-overrides.json"
ALLOWED_OVERRIDE_FIELDS = {"model", "max_turns", "reason"}


def load_recovery_overrides(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load validated run-local execution overrides.

    The file lives inside the ignored run directory so recovery tuning cannot
    silently rewrite the canonical public roster.
    """

    path = run_dir / RECOVERY_OVERRIDES_FILENAME
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Recovery overrides {path} are unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Recovery overrides {path} must contain an object")
    if value.get("version") != 1:
        raise ValueError(f"Recovery overrides {path} require version 1")
    if value.get("run_id") != run_dir.name:
        raise ValueError(f"Recovery overrides {path} do not match run {run_dir.name}")
    overrides = value.get("overrides")
    if not isinstance(overrides, dict):
        raise ValueError(f"Recovery overrides {path} require an overrides object")

    validated: dict[str, dict[str, Any]] = {}
    for agent_name, raw in overrides.items():
        if not isinstance(agent_name, str) or not agent_name:
            raise ValueError(f"Recovery overrides {path} contain an invalid agent name")
        if not isinstance(raw, dict):
            raise ValueError(f"Recovery override for {agent_name} must be an object")
        unexpected = set(raw) - ALLOWED_OVERRIDE_FIELDS
        if unexpected:
            raise ValueError(
                f"Recovery override for {agent_name} contains unsupported field "
                f"{sorted(unexpected)[0]!r}"
            )
        override: dict[str, Any] = {}
        if "model" in raw:
            model = raw["model"]
            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"Recovery override for {agent_name} has an invalid model")
            override["model"] = model.strip()
        if "max_turns" in raw:
            max_turns = raw["max_turns"]
            if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
                raise ValueError(f"Recovery override for {agent_name} has invalid max_turns")
            override["max_turns"] = max_turns
        if "reason" in raw:
            reason = raw["reason"]
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"Recovery override for {agent_name} has an invalid reason")
            override["reason"] = reason.strip()
        if not ({"model", "max_turns"} & set(override)):
            raise ValueError(f"Recovery override for {agent_name} changes no execution setting")
        validated[agent_name] = override
    return validated


def apply_recovery_override(
    agent: AgentRecord,
    overrides: dict[str, dict[str, Any]],
) -> AgentRecord:
    override = overrides.get(agent.name)
    if override is None:
        return agent
    changes = {
        field: override[field]
        for field in ("model", "max_turns")
        if field in override
    }
    return replace(agent, **changes)
