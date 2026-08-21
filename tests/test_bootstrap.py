from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"


class BootstrapTests(unittest.TestCase):
    def test_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(BOOTSTRAP)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recreates_stale_python_39_virtual_environment(self):
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            scripts_dir = temp_root / "scripts"
            stale_bin = temp_root / ".venv" / "bin"
            scripts_dir.mkdir(parents=True)
            stale_bin.mkdir(parents=True)

            # Exercise interpreter selection and stale-environment replacement while
            # stopping before package installation or network-dependent work.
            source = BOOTSTRAP.read_text()
            marker = "# Invoke the virtual environment by absolute path instead of relying on shell\n"
            prefix, separator, _ = source.partition(marker)
            self.assertTrue(separator, "bootstrap installation marker is missing")
            test_script = scripts_dir / "bootstrap.sh"
            test_script.write_text(prefix + "printf 'VENV_ONLY_TEST_OK\\n'\nexit 0\n")
            test_script.chmod(0o755)

            stale_python = stale_bin / "python"
            stale_python.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"-\" && \"${2:-}\" == \"3\" && \"${3:-}\" == \"11\" ]]; then\n"
                "  exit 1\n"
                "fi\n"
                "printf '3.9.6\\n'\n"
            )
            stale_python.chmod(0o755)
            (stale_bin / "python3").symlink_to(stale_python.name)

            env = os.environ.copy()
            env.pop("PYTHON", None)
            env.pop("PYTHON_BIN", None)
            env["VIRTUAL_ENV"] = str(temp_root / ".venv")
            env["PATH"] = f"{stale_bin}:{env.get('PATH', '')}"

            result = subprocess.run(
                [str(test_script)],
                cwd=temp_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Recreating", result.stdout)
            self.assertIn("Python 3.9.6", result.stdout)
            self.assertIn("VENV_ONLY_TEST_OK", result.stdout)

            recreated_python = temp_root / ".venv" / "bin" / "python"
            version_check = subprocess.run(
                [
                    str(recreated_python),
                    "-c",
                    "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
                ],
                check=False,
            )
            self.assertEqual(version_check.returncode, 0)

    def test_ignores_an_activated_stale_virtual_environment_during_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            scripts_dir = temp_root / "scripts"
            stale_bin = temp_root / ".venv" / "bin"
            base_bin = temp_root / "base-bin"
            scripts_dir.mkdir(parents=True)
            stale_bin.mkdir(parents=True)
            base_bin.mkdir()

            source = BOOTSTRAP.read_text()
            marker = "# Invoke the virtual environment by absolute path instead of relying on shell\n"
            prefix, separator, _ = source.partition(marker)
            self.assertTrue(separator, "bootstrap installation marker is missing")
            test_script = scripts_dir / "bootstrap.sh"
            test_script.write_text(prefix + "printf 'AUTO_DISCOVERY_TEST_OK\\n'\nexit 0\n")
            test_script.chmod(0o755)

            stale_interpreter = (
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"-\" && \"${2:-}\" == \"3\" && \"${3:-}\" == \"11\" ]]; then\n"
                "  exit 1\n"
                "fi\n"
                "printf '3.9.6\\n'\n"
            )
            for executable_name in ("python", "python3"):
                executable = stale_bin / executable_name
                executable.write_text(stale_interpreter)
                executable.chmod(0o755)

            (base_bin / "python3").symlink_to(sys.executable)
            env = os.environ.copy()
            env.pop("PYTHON", None)
            env.pop("PYTHON_BIN", None)
            env["VIRTUAL_ENV"] = str(temp_root / ".venv")
            env["PATH"] = os.pathsep.join(
                [str(stale_bin), str(base_bin), "/usr/bin", "/bin"]
            )

            result = subprocess.run(
                [str(test_script)],
                cwd=temp_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"at {base_bin / 'python3'}", result.stdout)
            self.assertIn("Ignoring active virtual environment", result.stdout)
            self.assertIn("Python 3.9.6", result.stdout)
            self.assertIn("AUTO_DISCOVERY_TEST_OK", result.stdout)

    def test_rejects_missing_requested_interpreter(self):
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            scripts_dir = temp_root / "scripts"
            scripts_dir.mkdir(parents=True)
            test_script = scripts_dir / "bootstrap.sh"
            shutil.copy2(BOOTSTRAP, test_script)

            result = subprocess.run(
                [str(test_script), "--python", "/definitely/missing/python"],
                cwd=temp_root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("was not found or is not executable", result.stderr)


    def test_hook_installer_supports_linked_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            repo = temp_root / "repo"
            worktree = temp_root / "worktree"

            repo.mkdir()

            subprocess.run(
                ["git", "init", "-q", "-b", "main"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Bootstrap Test"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "bootstrap@example.invalid"],
                cwd=repo,
                check=True,
            )

            (repo / "README.md").write_text("test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "initial"],
                cwd=repo,
                check=True,
            )

            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "test-worktree", str(worktree)],
                cwd=repo,
                check=True,
            )

            self.assertTrue((worktree / ".git").is_file())

            scripts_dir = worktree / "scripts"
            scripts_dir.mkdir()
            installer = scripts_dir / "install_git_hooks.sh"
            shutil.copy2(ROOT / "scripts" / "install_git_hooks.sh", installer)
            installer.chmod(0o755)

            result = subprocess.run(
                [str(installer)],
                cwd=worktree,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            hooks_path = subprocess.run(
                ["git", "rev-parse", "--git-path", "hooks"],
                cwd=worktree,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()

            hooks_dir = Path(hooks_path)
            if not hooks_dir.is_absolute():
                hooks_dir = worktree / hooks_dir

            hook = hooks_dir / "pre-commit"

            self.assertTrue(hook.is_file())
            self.assertTrue(hook.stat().st_mode & 0o111)
            self.assertIn(
                "scripts/privacy_check.py",
                hook.read_text(),
            )

    def test_release_archive_is_not_detected_as_git_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)

            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(temp_root),
                    "rev-parse",
                    "--is-inside-work-tree",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
