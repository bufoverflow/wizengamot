from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "examples/atlas"
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.models import LaunchPlan
from wizengamot.registry import load_agents
from wizengamot.runner import (
    DENIED_TOOLS,
    READ_ONLY_TOOLS,
    atomic_json,
    check_run_contract,
    execute_agent,
    launch_plan,
)


def fake_payload(agent, attempt: int, *, success: bool, cost: float) -> dict:
    return {
        "agent": {
            "name": agent.name,
            "title": agent.title,
            "tier": agent.tier,
            "domain": agent.domain,
            "role": agent.role,
        },
        "attempt": attempt,
        "result": {
            "subtype": "success" if success else "error_during_execution",
            "is_error": not success,
            "total_cost_usd": cost,
            "errors": [] if success else ["synthetic failure"],
            "report": {"agent_name": agent.name, "status": "complete"} if success else None,
        },
    }


def provenance_report(agent_name: str, *, valid: bool) -> dict:
    source_id = "ext-example-official-guide" if valid else "S1"
    return {
        "agent_name": agent_name,
        "status": "complete",
        "findings": [
            {
                "statement": "Example externally supported finding",
                "classification": "fact",
                "evidence_class": "external-primary",
                "novelty": "retrieved",
                "claim_type": "positive",
                "source_ids": [source_id],
                "reviewed_source_ids": [],
                "confidence": "high",
                "impact": "Exercises the runtime provenance contract.",
                "evidence": ["Official guide reviewed."],
            }
        ],
        "citations": [
            {
                "source_id": source_id,
                "label": "Official guide",
                "source_class": "external-primary",
                "publisher": "Example Publisher",
                "source": "Official guide",
                "locator": "https://example.com/official-guide",
                "date": "2026-08-29",
                "primary": True,
                "claims_supported": ["Example externally supported finding"],
            }
        ],
    }


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def test_check_run_contract_reports_mechanical_qualification(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "qualified-run"
            result_path = run_dir / "results" / "example-agent.json"
            payload = {
                "result": {
                    "is_error": False,
                    "report": provenance_report("example-agent", valid=True),
                },
            }
            atomic_json(result_path, payload)

            result = check_run_contract(run_dir)

            self.assertTrue(result["mechanically_qualified"])
            self.assertTrue(result["semantic_review_required"])
            self.assertEqual(result["reports"][0]["status"], "complete")
            self.assertEqual(result["reports"][0]["report_contract_errors"], [])

    def test_check_run_contract_rejects_missing_named_result(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "incomplete-run"
            (run_dir / "results").mkdir(parents=True)

            result = check_run_contract(run_dir, names=["missing-agent"])

            self.assertFalse(result["mechanically_qualified"])
            self.assertFalse(result["reports"][0]["exists"])
            self.assertEqual(result["reports"][0]["status"], "runner-error")

    def test_check_run_contract_rejects_unsafe_agent_name(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "unsafe-name-run"
            with self.assertRaisesRegex(ValueError, "Invalid result agent name"):
                check_run_contract(run_dir, names=["../outside"])

    async def test_execute_agent_constructs_bounded_ephemeral_options(self):
        agent = next(
            a for a in load_agents(WORKSPACE)
            if a.name == "atlas-research-quality-falsification-agent"
        )
        captured: dict = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class FakeResultMessage:
            subtype = "success"
            is_error = False
            session_id = "synthetic-session"
            num_turns = 1
            duration_ms = 10
            duration_api_ms = 5
            total_cost_usd = 0.01
            stop_reason = "end_turn"
            terminal_reason = "completed"
            permission_denials = None
            errors = None
            usage = {"input_tokens": 1, "output_tokens": 1}
            model_usage = {}
            structured_output = {
                "agent_name": agent.name,
                "status": "complete",
                "epistemic_notice": {
                    "model_generated": True,
                    "report_is_evidence": False,
                    "moves_empirical_validation": False,
                },
                "executive_summary": "synthetic",
                "findings": [],
                "risks": [],
                "falsifiers": [],
                "recommendations": [],
                "evidence_gaps": [],
                "citations": [],
                "actions": [
                    {
                        "description": "Interview an external operator",
                        "owner": "founder",
                        "target_date": "2026-08-22",
                        "action_type": "external",
                        "reversibility": "reversible",
                        "evidence_bar": "near-zero",
                        "rationale": "The interview itself generates the missing evidence.",
                    }
                ],
                "evidence_bar_review": {
                    "current_threshold": "near-zero",
                    "prior_threshold": None,
                    "drift_direction": "unknown",
                    "drift_basis": "unknown",
                    "recommendation": "Act and gather external evidence.",
                },
                "handoff": [],
            }
            result = None

        async def fake_query(*, prompt, options):
            self.assertIn(agent.name, prompt)
            self.assertIsInstance(options, FakeOptions)
            yield FakeResultMessage()

        fake_sdk = types.ModuleType("claude_agent_sdk")
        fake_sdk.ClaudeAgentOptions = FakeOptions
        fake_sdk.ResultMessage = FakeResultMessage
        fake_sdk.query = fake_query

        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            output_path = Path(td) / "attempt-1.json"
            payload = await execute_agent(
                root=WORKSPACE,
                agent=agent,
                task="Synthetic option-construction test",
                campaign_prompt=None,
                output_path=output_path,
                max_budget_usd=0.50,
                max_turns=None,
                attempt=1,
            )

        self.assertEqual(captured["tools"], READ_ONLY_TOOLS)
        self.assertEqual(captured["allowed_tools"], READ_ONLY_TOOLS)
        self.assertEqual(captured["disallowed_tools"], DENIED_TOOLS)
        self.assertTrue(captured["strict_mcp_config"])
        self.assertEqual(captured["permission_mode"], "dontAsk")
        self.assertEqual(captured["setting_sources"], ["project"])
        self.assertEqual(captured["skills"], [])
        self.assertEqual(captured["env"]["CLAUDE_CODE_SKIP_PROMPT_HISTORY"], "1")
        self.assertEqual(captured["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(captured["env"]["CLAUDE_CODE_FORK_SUBAGENT"], "0")
        self.assertEqual(captured["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"], "1")
        self.assertEqual(captured["env"]["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertEqual(payload["result"]["report"]["agent_name"], agent.name)
        self.assertFalse(payload["result"]["report"]["epistemic_notice"]["report_is_evidence"])

    async def test_retry_history_cost_and_successful_resume_skip(self):
        agent = next(
            a for a in load_agents(WORKSPACE)
            if a.name == "atlas-research-quality-falsification-agent"
        )
        calls: list[int] = []

        async def fake_execute_agent(**kwargs):
            attempt = kwargs["attempt"]
            calls.append(attempt)
            payload = fake_payload(agent, attempt, success=attempt >= 2, cost=0.10 * attempt)
            atomic_json(kwargs["output_path"], payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "retry-run"
            plan = LaunchPlan(
                agents=(agent,), task="Synthetic retry test", concurrency=1,
                per_agent_budget_usd=0.50, retries=1, aggregate_ceiling_usd=1.0,
                run_dir=run_dir,
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                first = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )
                second = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(calls, [1, 2])
            self.assertEqual(first["succeeded"], 1)
            self.assertEqual(first["attempt_count"], 2)
            self.assertAlmostEqual(first["estimated_total_cost_usd"], 0.30)
            self.assertEqual(second["skipped_successful"], 1)
            self.assertEqual(len(json.loads((run_dir / "manifest.json").read_text())["launch_history"]), 2)

    async def test_failed_result_is_retried_on_resume(self):
        agent = next(a for a in load_agents(WORKSPACE) if a.name == "atlas-council-adversarial-review")
        outcomes = iter([False, True])
        calls: list[int] = []

        async def fake_execute_agent(**kwargs):
            attempt = kwargs["attempt"]
            calls.append(attempt)
            payload = fake_payload(agent, attempt, success=next(outcomes), cost=0.20)
            atomic_json(kwargs["output_path"], payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "resume-run"
            plan = LaunchPlan(
                agents=(agent,), task="Synthetic resume test", concurrency=1,
                per_agent_budget_usd=0.50, retries=0, aggregate_ceiling_usd=0.50,
                run_dir=run_dir,
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                first = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )
                second = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(first["failed"], 1)
            self.assertEqual(second["succeeded"], 1)
            self.assertEqual(calls, [1, 2])
            final = json.loads((run_dir / "results" / f"{agent.name}.json").read_text())
            self.assertFalse(final["result"]["is_error"])

    async def test_contract_error_is_retried_with_repair_feedback(self):
        agent = next(
            a for a in load_agents(WORKSPACE)
            if a.name == "atlas-research-quality-verifier-auditor"
        )
        calls: list[tuple[int, list[str] | None]] = []

        async def fake_execute_agent(**kwargs):
            attempt = kwargs["attempt"]
            feedback = kwargs["prior_report_contract_errors"]
            calls.append((attempt, feedback))
            payload = {
                "agent": {"name": agent.name},
                "attempt": attempt,
                "result": {
                    "subtype": "success",
                    "is_error": False,
                    "total_cost_usd": 0.10,
                    "errors": [],
                    "report": provenance_report(agent.name, valid=attempt >= 2),
                },
            }
            atomic_json(kwargs["output_path"], payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "contract-retry-run"
            plan = LaunchPlan(
                agents=(agent,), task="Synthetic contract retry test", concurrency=1,
                per_agent_budget_usd=0.50, retries=1, aggregate_ceiling_usd=1.0,
                run_dir=run_dir,
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                summary = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(calls[0], (1, None))
            self.assertTrue(calls[1][1])
            self.assertTrue(any("is generic" in error for error in calls[1][1]))
            self.assertEqual(summary["succeeded"], 1)
            self.assertEqual(summary["attempt_count"], 2)
            first_attempt = json.loads(
                (run_dir / "attempts" / agent.name / "attempt-1.json").read_text()
            )
            final = json.loads((run_dir / "results" / f"{agent.name}.json").read_text())
            self.assertTrue(first_attempt["report_contract_errors"])
            self.assertEqual(final["report_contract_errors"], [])

    async def test_exhausted_contract_error_is_saved_and_reported_in_progress(self):
        agent = next(
            a for a in load_agents(WORKSPACE)
            if a.name == "atlas-research-quality-verifier-auditor"
        )

        async def fake_execute_agent(**kwargs):
            payload = {
                "agent": {"name": agent.name},
                "attempt": kwargs["attempt"],
                "result": {
                    "subtype": "success",
                    "is_error": False,
                    "total_cost_usd": 0.10,
                    "errors": [],
                    "report": provenance_report(agent.name, valid=False),
                },
            }
            atomic_json(kwargs["output_path"], payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "contract-error-run"
            plan = LaunchPlan(
                agents=(agent,), task="Synthetic contract failure test", concurrency=1,
                per_agent_budget_usd=0.50, retries=0, aggregate_ceiling_usd=0.50,
                run_dir=run_dir,
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                summary = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            progress = json.loads((run_dir / "progress.json").read_text())
            final = json.loads((run_dir / "results" / f"{agent.name}.json").read_text())
            self.assertEqual(summary["succeeded"], 0)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["report_status_counts"], {"contract-error": 1})
            self.assertEqual(progress["report_status_counts"], {"contract-error": 1})
            self.assertTrue(final["report_contract_errors"])


if __name__ == "__main__":
    unittest.main()
