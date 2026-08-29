from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "examples/atlas"
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.cli import main


class CliTests(unittest.TestCase):
    def test_explicit_zero_budget_is_rejected(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main([
                "--workspace", str(WORKSPACE),
                "plan", "--campaign", "full-audit", "--max-agent-budget", "0",
            ])
        self.assertEqual(code, 2)
        self.assertIn("must be positive", stderr.getvalue())

    def test_workspace_command(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--workspace", str(WORKSPACE), "workspace"])
        self.assertEqual(code, 0)
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["name"], "atlas")

    def test_run_id_rejects_path_traversal(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main([
                "--workspace", str(WORKSPACE),
                "launch",
                "--name", "atlas-research-quality-falsification-agent",
                "--task", "Synthetic run-id validation test",
                "--concurrency", "1",
                "--max-agent-budget", "0.50",
                "--max-total-budget", "0.50",
                "--run-id", "../escape",
                "--execute",
            ])
        self.assertEqual(code, 2)
        self.assertIn("Run ID must start", stderr.getvalue())

    def test_check_run_contract_rejects_agent_name_path_traversal(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main([
                "--workspace", str(WORKSPACE),
                "check-run-contract",
                "--run-id", "synthetic-run",
                "--name", "../outside",
            ])
        self.assertEqual(code, 2)
        self.assertIn("Invalid result agent name", stderr.getvalue())

    def test_launch_defaults_to_dry_run(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main([
                "--workspace", str(WORKSPACE),
                "launch",
                "--name", "atlas-research-quality-verifier-auditor",
                "--task", "Synthetic dry run",
                "--max-agent-budget", "0.25",
                "--max-total-budget", "0.25",
            ])
        self.assertEqual(code, 0)
        self.assertIn("Dry run only", stdout.getvalue())

    def test_global_provider_pause_exits_nonzero_with_resume_message(self):
        summary = {
            "run_id": "paused-cli-run",
            "status": "paused",
            "failed": 1,
            "pause": {
                "error": "Your organization quota has been exhausted.",
                "reset_hint": "Restore the Claude account quota before resuming.",
            },
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("wizengamot.cli.launch_plan", new=AsyncMock(return_value=summary)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main([
                "--workspace", str(WORKSPACE),
                "launch",
                "--name", "atlas-research-quality-verifier-auditor",
                "--task", "Synthetic paused CLI test",
                "--concurrency", "1",
                "--max-agent-budget", "0.50",
                "--max-total-budget", "0.50",
                "--run-id", "paused-cli-run",
                "--analysis-exempt", "incident-recovery",
                "--execute",
            ])

        self.assertEqual(code, 1)
        self.assertIn("Run paused:", stderr.getvalue())
        self.assertIn("same --run-id paused-cli-run", stderr.getvalue())
        self.assertIn('"status": "paused"', stdout.getvalue())

    def _large_workspace(self, root: Path) -> Path:
        workspace = root / "large"
        (workspace / "registry").mkdir(parents=True)
        (workspace / "agent-library").mkdir()
        (workspace / "schemas").mkdir()
        (workspace / "campaigns").mkdir()
        (workspace / "tasks").mkdir()
        (workspace / ".claude/agents/activated").mkdir(parents=True)
        (workspace / "CLAUDE.md").write_text("# Synthetic\n")
        (workspace / "context.md").write_text("synthetic\n")
        (workspace / "agent-library/template.md").write_text(
            "---\nname: synthetic-template\ndescription: synthetic\n---\nbody\n"
        )
        schema = json.loads((WORKSPACE / "schemas/agent-result.schema.json").read_text())
        (workspace / "schemas/agent-result.schema.json").write_text(json.dumps(schema))
        agents = []
        for index in range(100):
            agents.append({
                "name": f"synthetic-agent-{index}",
                "title": f"Synthetic {index}",
                "tier": "specialist",
                "domain": "synthetic",
                "role": "worker",
                "description": "synthetic",
                "model": "haiku",
                "max_turns": 1,
                "recommended_budget_usd": 0.01,
                "effort": "medium",
                "tools": ["Read"],
                "disallowed_tools": ["Agent", "Bash", "Edit", "Write"],
                "library_path": "agent-library/template.md",
                "interactive_path": None,
                "context": ["context.md"],
            })
        (workspace / "registry/agents.json").write_text(json.dumps(agents))
        (workspace / "wizengamot.workspace.json").write_text(json.dumps({
            "name": "large",
            "expected_counts": {"specialist": 100},
            "default_interactive_count": 0,
            "output_schema": "schemas/agent-result.schema.json",
            "instruction_files": ["CLAUDE.md"],
            "required_context": ["context.md"],
        }))
        return workspace

    def test_large_execution_requires_exact_acknowledgement(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = self._large_workspace(Path(td))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main([
                    "--workspace", str(workspace),
                    "launch",
                    "--task", "Synthetic large-run gate",
                    "--concurrency", "10",
                    "--max-agent-budget", "0.01",
                    "--max-total-budget", "1.00",
                    "--execute",
                ])
            self.assertEqual(code, 2)
            self.assertIn("--ack-large-run 100", stderr.getvalue())

    def test_large_activation_requires_override(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = self._large_workspace(Path(td))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main([
                    "--workspace", str(workspace),
                    "activate", "--tier", "specialist",
                ])
            self.assertEqual(code, 2)
            self.assertIn("Use the batch runner", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
