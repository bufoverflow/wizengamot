from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wizengamot.governance import (
    ACTION_LEDGER,
    STATE_FILE,
    analysis_guard_blocked,
    load_analysis_state,
    note_analysis_run,
    record_external_action,
)


class GovernanceTests(unittest.TestCase):
    def test_analysis_runs_increment_and_guard_blocks_at_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            blocked, count = analysis_guard_blocked(root, max_consecutive_runs=2)
            self.assertFalse(blocked)
            self.assertEqual(count, 0)

            note_analysis_run(root)
            blocked, count = analysis_guard_blocked(root, max_consecutive_runs=2)
            self.assertFalse(blocked)
            self.assertEqual(count, 1)

            note_analysis_run(root)
            blocked, count = analysis_guard_blocked(root, max_consecutive_runs=2)
            self.assertTrue(blocked)
            self.assertEqual(count, 2)

    def test_external_action_resets_analysis_counter_and_writes_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            note_analysis_run(root)
            note_analysis_run(root)

            record = record_external_action(
                root,
                description="Interview an external operator",
                owner="founder",
                occurred_at="2026-08-21T12:00:00+00:00",
            )

            state = load_analysis_state(root)
            self.assertEqual(state["consecutive_analysis_runs"], 0)
            self.assertEqual(
                state["last_external_action_at"],
                "2026-08-21T12:00:00+00:00",
            )
            self.assertEqual(record["action_type"], "external")

            ledger = root / ACTION_LEDGER
            self.assertTrue(ledger.is_file())
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["description"], "Interview an external operator")

            state_path = root / STATE_FILE
            self.assertTrue(state_path.is_file())


if __name__ == "__main__":
    unittest.main()
