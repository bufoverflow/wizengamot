#!/usr/bin/env python3
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.registry import load_agents
from wizengamot.workspace import resolve_workspace


def main() -> int:
    workspace = resolve_workspace()
    agents = load_agents(workspace)
    print(f"workspace: {workspace}")
    print(f"total: {len(agents)}")
    for tier, count in sorted(Counter(a.tier for a in agents).items()):
        print(f"{tier}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
