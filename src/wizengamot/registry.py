from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import AgentRecord, Campaign
from .workspace import resolve_workspace


def workspace_root(start: Path | None = None) -> Path:
    return resolve_workspace(start=start)


def load_agents(root: Path | None = None) -> list[AgentRecord]:
    base = root or workspace_root()
    values = json.loads((base / "registry/agents.json").read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise ValueError("registry/agents.json must contain a JSON array")
    return [AgentRecord.from_dict(value) for value in values]


def load_campaign(name: str, root: Path | None = None) -> Campaign:
    base = root or workspace_root()
    path = base / "campaigns" / f"{name}.json"
    if not path.exists():
        available = sorted(p.stem for p in (base / "campaigns").glob("*.json"))
        raise ValueError(f"Unknown campaign {name!r}. Available: {', '.join(available)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Campaign {path} must contain a JSON object")
    return Campaign.from_dict(value)


def _matches(value: str, selected: set[str]) -> bool:
    return not selected or "*" in selected or value in selected


def select_agents(
    agents: Iterable[AgentRecord],
    *,
    tiers: Iterable[str] = (),
    domains: Iterable[str] = (),
    roles: Iterable[str] = (),
    names: Iterable[str] = (),
) -> list[AgentRecord]:
    tier_set, domain_set, role_set, name_set = map(set, (tiers, domains, roles, names))
    structured_filters = bool(tier_set or domain_set or role_set)
    chosen: dict[str, AgentRecord] = {}
    for agent in agents:
        selected = agent.name in name_set
        if structured_filters:
            tier_ok = _matches(agent.tier, tier_set)
            domain_ok = _matches(agent.domain, domain_set)
            role_ok = _matches(agent.role, role_set)
            if tier_ok:
                if agent.tier == "specialist":
                    selected = selected or (domain_ok and role_ok)
                elif agent.tier == "director":
                    selected = selected or domain_ok
                else:
                    selected = selected or (domain_ok and role_ok)
        if selected:
            chosen[agent.name] = agent
    return sorted(chosen.values(), key=lambda a: (a.tier, a.domain, a.role, a.name))


def select_campaign(campaign: Campaign, agents: Iterable[AgentRecord]) -> list[AgentRecord]:
    selectors = campaign.selectors
    return select_agents(
        agents,
        tiers=selectors.get("tiers", []),
        domains=selectors.get("domains", []),
        roles=selectors.get("roles", []),
        names=selectors.get("names", []),
    )
