from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "examples/atlas"
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.registry import load_agents, load_campaign, select_agents, select_campaign
from wizengamot.validation import validate


class RegistryTests(unittest.TestCase):
    def test_expected_inventory(self):
        agents = load_agents(WORKSPACE)
        self.assertEqual(len(agents), 17)
        self.assertEqual(len({a.name for a in agents}), 17)

    def test_full_audit_selection(self):
        selected = select_campaign(load_campaign("full-audit", WORKSPACE), load_agents(WORKSPACE))
        self.assertEqual(len(selected), 15)
        self.assertFalse(any(a.tier in {"chief", "synthesis"} for a in selected))

    def test_exact_name_selection(self):
        selected = select_agents(load_agents(WORKSPACE), names=["atlas-council-adversarial-review"])
        self.assertEqual([a.name for a in selected], ["atlas-council-adversarial-review"])

    def test_static_validation(self):
        self.assertEqual(validate(WORKSPACE), [])


if __name__ == "__main__":
    unittest.main()
