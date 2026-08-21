#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.registry import load_agents
from wizengamot.validation import validate
from wizengamot.workspace import load_workspace_config, resolve_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a Wizengamot workspace, its agent registry, context, schema, and campaigns"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace containing wizengamot.workspace.json; defaults to normal workspace resolution",
    )
    args = parser.parse_args(argv)

    try:
        root = resolve_workspace(args.workspace)
        errors = validate(root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Validation failed to run: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    config = load_workspace_config(root)
    audit = ""
    if config.audit_campaign:
        audit = f"; {config.audit_worker_count} {config.audit_campaign} workers"
    print(
        f"Validation passed for {config.display_name}: {len(load_agents(root))} agents; "
        f"{config.default_interactive_count or 0} default interactive definitions{audit}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
