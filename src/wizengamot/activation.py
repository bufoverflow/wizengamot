from __future__ import annotations

import shutil
from pathlib import Path

from .models import AgentRecord
from .registry import workspace_root


def clear_activated(root: Path | None = None) -> int:
    base = root or workspace_root()
    target = base / ".claude/agents/activated"
    target.mkdir(parents=True, exist_ok=True)
    removed = 0
    for path in target.rglob("*.md"):
        path.unlink()
        removed += 1
    for directory in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    (target / ".gitkeep").touch()
    return removed


def activate(agents: list[AgentRecord], root: Path | None = None, clear: bool = False) -> list[Path]:
    base = root or workspace_root()
    if clear:
        clear_activated(base)
    target = base / ".claude/agents/activated"
    target.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for agent in agents:
        if agent.interactive_path:
            continue
        source = base / agent.library_path
        destination = target / agent.tier / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied
