from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "examples/atlas"
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.workspace import load_workspace_config, resolve_workspace


class WorkspaceTests(unittest.TestCase):
    def test_explicit_workspace(self):
        self.assertEqual(resolve_workspace(WORKSPACE), WORKSPACE.resolve())

    def test_nearest_workspace_marker(self):
        self.assertEqual(resolve_workspace(start=WORKSPACE / "context"), WORKSPACE.resolve())

    def test_environment_workspace(self):
        with patch.dict(os.environ, {"WIZENGAMOT_WORKSPACE": str(WORKSPACE)}):
            self.assertEqual(resolve_workspace(start=ROOT), WORKSPACE.resolve())

    def test_workspace_config(self):
        config = load_workspace_config(WORKSPACE)
        self.assertEqual(config.name, "atlas")
        self.assertEqual(config.audit_worker_count, 15)


if __name__ == "__main__":
    unittest.main()
