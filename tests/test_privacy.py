from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_public_release import selected_files
import privacy_check
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


    def test_staged_scan_reads_index_blob_not_working_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)

            subprocess.run(
                ["git", "init", "-q"],
                cwd=repo,
                check=True,
            )

            probe = repo / "probe.txt"
            probe.write_text(
                "sk-" + "ant-" + "this-is-a-synthetic-secret-value\n"
            )

            subprocess.run(
                ["git", "add", "probe.txt"],
                cwd=repo,
                check=True,
            )

            # Make the working-tree copy clean while leaving the secret staged.
            probe.write_text("clean working tree content\n")

            with mock.patch.object(privacy_check, "ROOT", repo):
                working_tree_errors = privacy_check.scan([probe])
                staged_errors = privacy_check.scan([probe], staged=True)

            self.assertEqual(working_tree_errors, [])
            self.assertTrue(
                any("Anthropic API key" in error for error in staged_errors),
                staged_errors,
            )


if __name__ == "__main__":
    unittest.main()
