from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_FILE = ".wizengamot-analysis-state.json"
ACTION_LEDGER = "action-ledger.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(root: Path) -> Path:
    return root / STATE_FILE


def load_analysis_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {
            "consecutive_analysis_runs": 0,
            "last_external_action_at": None,
            "last_analysis_run_at": None,
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "consecutive_analysis_runs": 0,
            "last_external_action_at": None,
            "last_analysis_run_at": None,
        }
    if not isinstance(value, dict):
        return {
            "consecutive_analysis_runs": 0,
            "last_external_action_at": None,
            "last_analysis_run_at": None,
        }
    return value


def _write_state(root: Path, value: dict[str, Any]) -> None:
    path = _state_path(root)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def note_analysis_run(root: Path) -> dict[str, Any]:
    state = load_analysis_state(root)
    count = int(state.get("consecutive_analysis_runs", 0)) + 1
    state["consecutive_analysis_runs"] = count
    state["last_analysis_run_at"] = utc_now()
    _write_state(root, state)
    return state


def record_external_action(
    root: Path,
    *,
    description: str,
    owner: str,
    occurred_at: str | None,
) -> dict[str, Any]:
    now = utc_now()
    record = {
        "recorded_at": now,
        "occurred_at": occurred_at or now,
        "action_type": "external",
        "description": description,
        "owner": owner,
    }
    ledger = root / ACTION_LEDGER
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    state = load_analysis_state(root)
    state["consecutive_analysis_runs"] = 0
    state["last_external_action_at"] = record["occurred_at"]
    _write_state(root, state)
    return record


def analysis_guard_blocked(root: Path, *, max_consecutive_runs: int) -> tuple[bool, int]:
    state = load_analysis_state(root)
    count = int(state.get("consecutive_analysis_runs", 0))
    return count >= max_consecutive_runs, count
