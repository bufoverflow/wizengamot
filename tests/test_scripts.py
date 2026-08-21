from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "examples" / "atlas"


class ScriptTests(unittest.TestCase):
    def test_validate_script_honors_explicit_workspace(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "validate.py"),
                "--workspace",
                str(ATLAS),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Atlas Field Study", result.stdout)
        self.assertIn("17 agents", result.stdout)


if __name__ == "__main__":
    unittest.main()
