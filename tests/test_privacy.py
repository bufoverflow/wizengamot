from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_public_release import selected_files
from privacy_check import load_public_files, scan


class PrivacyTests(unittest.TestCase):
    def test_public_allowlist_passes(self):
        self.assertEqual(scan(load_public_files()), [])

    def test_public_release_excludes_private_workspace(self):
        rels = {path.relative_to(ROOT).as_posix() for path in selected_files()}
        self.assertFalse(any(rel.startswith("workspace/") for rel in rels))
        self.assertFalse(any("/knowledge/" in f"/{rel}/" for rel in rels))

    def test_secret_pattern_is_rejected(self):
        path = ROOT / "tests" / "synthetic-secret.tmp"
        try:
            path.write_text("ANTHROPIC_API_KEY=" + "sk-" + "ant-" + "this-is-a-synthetic-secret-value\n")
            errors = scan([path])
            self.assertTrue(any("API key" in error for error in errors))
        finally:
            path.unlink(missing_ok=True)

    def test_private_document_filename_is_rejected(self):
        path = ROOT / "tests" / "roadmap.confidential.md"
        try:
            path.write_text("synthetic private planning material\n")
            errors = scan([path])
            self.assertTrue(any("private document filename" in error for error in errors))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
