from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "examples/atlas"
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.activation import activate, clear_activated
from wizengamot.registry import load_agents


class ActivationTests(unittest.TestCase):
    def tearDown(self):
        clear_activated(WORKSPACE)

    def test_default_interactive_agent_is_not_duplicated(self):
        agent = next(a for a in load_agents(WORKSPACE) if a.name == "atlas-chief")
        copied = activate([agent], WORKSPACE, clear=True)
        self.assertEqual(copied, [])

    def test_activate_one(self):
        agent = next(
            a for a in load_agents(WORKSPACE)
            if a.name == "atlas-research-quality-falsification-agent"
        )
        copied = activate([agent], WORKSPACE, clear=True)
        self.assertEqual(len(copied), 1)
        self.assertTrue(copied[0].exists())
        self.assertIn("activated", copied[0].parts)


if __name__ == "__main__":
    unittest.main()
