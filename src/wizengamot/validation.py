from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from .registry import load_agents, load_campaign, select_campaign
from .workspace import load_workspace_config, resolve_workspace

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TIERS = {"chief", "director", "specialist", "council", "synthesis"}


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Missing YAML frontmatter: {path}")
    chunks = text.split("---", 2)
    if len(chunks) != 3:
        raise ValueError(f"Unterminated YAML frontmatter: {path}")
    result: dict[str, str] = {}
    for raw in chunks[1].strip().splitlines():
        if not raw.strip():
            continue
        if ":" not in raw:
            raise ValueError(f"Malformed frontmatter line in {path}: {raw}")
        key, value = raw.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def validate(root: Path | None = None) -> list[str]:
    base = root or resolve_workspace()
    errors: list[str] = []
    try:
        config = load_workspace_config(base)
    except Exception as exc:
        return [f"Invalid workspace configuration: {exc}"]

    try:
        agents = load_agents(base)
    except Exception as exc:
        return [f"Invalid agent registry: {exc}"]

    counts = Counter(a.tier for a in agents)
    if config.expected_counts:
        expected_total = sum(config.expected_counts.values())
        if len(agents) != expected_total:
            errors.append(f"Expected {expected_total} agents, found {len(agents)}")
        for tier, expected in config.expected_counts.items():
            if counts[tier] != expected:
                errors.append(f"Expected {expected} {tier} agents, found {counts[tier]}")

    names = [a.name for a in agents]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        errors.append(f"Duplicate agent names: {duplicates}")

    for agent in agents:
        if agent.tier not in TIERS:
            errors.append(f"Invalid tier for {agent.name}: {agent.tier}")
        if not NAME_RE.fullmatch(agent.name) or ":" in agent.name:
            errors.append(f"Invalid agent name: {agent.name}")
        for label, rel in (("library_path", agent.library_path), ("interactive_path", agent.interactive_path)):
            if rel is not None and not _safe_relative(rel):
                errors.append(f"Unsafe {label} for {agent.name}: {rel}")
        if not _safe_relative(agent.library_path):
            continue
        path = base / agent.library_path
        if not path.exists():
            errors.append(f"Missing agent file: {agent.library_path}")
            continue
        try:
            fm = parse_frontmatter(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        declared = fm.get("name", "").strip('"')
        if declared != agent.name:
            errors.append(f"Frontmatter name mismatch for {agent.library_path}: {declared!r}")
        if "description" not in fm:
            errors.append(f"Missing description in {agent.library_path}")
        for context_path in agent.context:
            if not _safe_relative(context_path):
                errors.append(f"Unsafe context path for {agent.name}: {context_path}")
            elif not (base / context_path).exists():
                errors.append(f"Missing context file for {agent.name}: {context_path}")

    for rel in (*config.instruction_files, *config.required_context):
        if not _safe_relative(rel):
            errors.append(f"Unsafe workspace context path: {rel}")
        elif not (base / rel).exists():
            errors.append(f"Missing workspace context file: {rel}")

    interactive = list((base / ".claude/agents").rglob("*.md")) if (base / ".claude/agents").exists() else []
    active = [p for p in interactive if "activated" not in p.parts]
    if config.default_interactive_count is not None and len(active) != config.default_interactive_count:
        errors.append(
            f"Expected {config.default_interactive_count} default interactive agents, found {len(active)}"
        )

    try:
        schema = json.loads((base / config.output_schema).read_text(encoding="utf-8"))
        if schema.get("type") != "object":
            errors.append("Output schema must be an object")
    except Exception as exc:
        errors.append(f"Invalid output schema: {exc}")

    campaign_dir = base / "campaigns"
    if campaign_dir.exists():
        for path in sorted(campaign_dir.glob("*.json")):
            try:
                selected = select_campaign(load_campaign(path.stem, base), agents)
                if not selected:
                    errors.append(f"Campaign {path.stem} selects zero agents")
            except Exception as exc:
                errors.append(f"Campaign {path.stem} is invalid: {exc}")

    if config.audit_campaign:
        try:
            selected = select_campaign(load_campaign(config.audit_campaign, base), agents)
            if config.audit_worker_count is not None and len(selected) != config.audit_worker_count:
                errors.append(
                    f"Audit campaign {config.audit_campaign} should select "
                    f"{config.audit_worker_count} workers, found {len(selected)}"
                )
        except Exception as exc:
            errors.append(f"Audit campaign validation failed: {exc}")
    return errors
