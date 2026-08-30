from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "examples/atlas"
sys.path.insert(0, str(ROOT / "src"))

from wizengamot.models import LaunchPlan, PostSourceStage
from wizengamot.registry import load_agents
from wizengamot.runner import (
    DENIED_TOOLS,
    READ_ONLY_TOOLS,
    atomic_json,
    check_run_contract,
    classify_global_provider_failure,
    execute_agent,
    launch_plan,
    launch_post_source_pipeline,
    post_source_pipeline_ceiling,
    session_limit_wait_seconds,
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


def global_error_payload(agent, attempt: int, error: str) -> dict:
    return {
        "agent": {"name": agent.name},
        "attempt": attempt,
        "result": {
            "subtype": "error_during_execution",
            "is_error": True,
            "total_cost_usd": 0.0,
            "errors": [error],
            "report": None,
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
    async def test_post_source_pipeline_runs_fable_then_opus_after_contract_gate(self):
        agents = tuple(load_agents(WORKSPACE))
        source_agent = next(
            agent for agent in agents
            if agent.name == "atlas-research-quality-falsification-agent"
        )
        fable_template = next(
            agent for agent in agents
            if agent.name == "atlas-synthesis-decision-memo"
        )
        opus_template = next(
            agent for agent in agents
            if agent.name == "atlas-council-adversarial-review"
        )
        stages = (
            PostSourceStage(
                name="frontier-synthesis",
                agent_name=fable_template.name,
                model="claude-fable-5",
                effort="max",
                max_turns=40,
                budget_usd=10.0,
                retries=1,
                task="Produce the master decision.",
            ),
            PostSourceStage(
                name="opus-challenge",
                agent_name=opus_template.name,
                model="claude-opus-5",
                effort="max",
                max_turns=32,
                budget_usd=5.0,
                retries=1,
                task="Challenge the master decision.",
            ),
        )
        calls: list[LaunchPlan] = []

        async def fake_launch_plan(**kwargs):
            stage_plan = kwargs["plan"]
            calls.append(stage_plan)
            stage_agent = stage_plan.agents[0]
            result_path = stage_plan.run_dir / "results" / f"{stage_agent.name}.json"
            atomic_json(result_path, fake_payload(stage_agent, 1, success=True, cost=0.25))
            return {
                "run_id": stage_plan.run_dir.name,
                "status": "complete",
                "succeeded": 1,
                "failed": 0,
                "deferred": 0,
                "estimated_total_cost_usd": 0.25,
                "session_reset_count": 0,
            }

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "source-run"
            source_plan = LaunchPlan(
                agents=(source_agent,),
                task="Synthetic source task",
                concurrency=1,
                per_agent_budget_usd=0.5,
                retries=0,
                aggregate_ceiling_usd=0.5,
                run_dir=run_dir,
            )
            atomic_json(
                run_dir / "results" / f"{source_agent.name}.json",
                fake_payload(source_agent, 1, success=True, cost=0.1),
            )
            with patch("wizengamot.runner.launch_plan", side_effect=fake_launch_plan):
                result = await launch_post_source_pipeline(
                    root=WORKSPACE,
                    source_plan=source_plan,
                    source_summary={
                        "status": "complete",
                        "failed": 0,
                        "deferred": 0,
                    },
                    stages=stages,
                    agents=agents,
                )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["estimated_total_cost_usd"], 0.5)
            self.assertEqual([plan.agents[0].model for plan in calls], [
                "claude-fable-5",
                "claude-opus-5",
            ])
            self.assertEqual([plan.agents[0].effort for plan in calls], ["max", "max"])
            self.assertIn("source-run/results", calls[0].task)
            self.assertIn("frontier-synthesis", calls[1].task)
            self.assertIn("atlas-synthesis-decision-memo.json", calls[1].task)
            self.assertTrue(result["final_result_path"].endswith(
                "atlas-council-adversarial-review.json"
            ))
            saved = json.loads((run_dir / "post-source-pipeline.json").read_text())
            self.assertEqual(saved["definition"]["source_run_id"], "source-run")
            self.assertEqual(len(saved["stages"]), 2)
            corpus_index = json.loads((run_dir / "source-corpus-index.json").read_text())
            self.assertEqual(corpus_index["report_count"], 1)
            self.assertEqual(
                corpus_index["source_corpus_sha256"],
                saved["definition"]["source_corpus_sha256"],
            )
            self.assertEqual(corpus_index["reports"][0]["agent_name"], source_agent.name)

    async def test_post_source_pipeline_does_not_run_on_invalid_source_contract(self):
        agents = tuple(load_agents(WORKSPACE))
        source_agent = next(iter(agents))
        stage = PostSourceStage(
            name="frontier-synthesis",
            agent_name="atlas-synthesis-decision-memo",
            model="claude-fable-5",
            effort="max",
            max_turns=36,
            budget_usd=10.0,
            retries=1,
            task="Produce the master decision.",
        )
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "invalid-source-run"
            source_plan = LaunchPlan(
                agents=(source_agent,),
                task="Synthetic source task",
                concurrency=1,
                per_agent_budget_usd=0.5,
                retries=0,
                aggregate_ceiling_usd=0.5,
                run_dir=run_dir,
            )
            (run_dir / "results").mkdir(parents=True)
            with patch("wizengamot.runner.launch_plan") as mocked_launch:
                result = await launch_post_source_pipeline(
                    root=WORKSPACE,
                    source_plan=source_plan,
                    source_summary={
                        "status": "complete",
                        "failed": 0,
                        "deferred": 0,
                    },
                    stages=(stage,),
                    agents=agents,
                )

            self.assertEqual(result["status"], "blocked-source-contract")
            self.assertFalse(result["source_contract"]["mechanically_qualified"])
            mocked_launch.assert_not_called()

            atomic_json(
                run_dir / "results" / f"{source_agent.name}.json",
                fake_payload(source_agent, 1, success=True, cost=0.1),
            )

            async def fake_stage_launch(**kwargs):
                stage_plan = kwargs["plan"]
                stage_agent = stage_plan.agents[0]
                atomic_json(
                    stage_plan.run_dir / "results" / f"{stage_agent.name}.json",
                    fake_payload(stage_agent, 1, success=True, cost=0.2),
                )
                return {
                    "status": "complete",
                    "succeeded": 1,
                    "failed": 0,
                    "deferred": 0,
                    "estimated_total_cost_usd": 0.2,
                    "session_reset_count": 0,
                }

            with patch("wizengamot.runner.launch_plan", side_effect=fake_stage_launch):
                resumed = await launch_post_source_pipeline(
                    root=WORKSPACE,
                    source_plan=source_plan,
                    source_summary={"status": "complete", "failed": 0, "deferred": 0},
                    stages=(stage,),
                    agents=agents,
                )
            self.assertEqual(resumed["status"], "complete")

    async def test_post_source_pipeline_resume_skips_completed_stages(self):
        agents = tuple(load_agents(WORKSPACE))
        source_agent = next(
            agent for agent in agents
            if agent.name == "atlas-research-quality-falsification-agent"
        )
        stages = (
            PostSourceStage(
                name="frontier-synthesis",
                agent_name="atlas-synthesis-decision-memo",
                model="claude-fable-5",
                effort="max",
                max_turns=40,
                budget_usd=10.0,
                retries=1,
                task="Produce the master decision.",
            ),
            PostSourceStage(
                name="opus-challenge",
                agent_name="atlas-council-adversarial-review",
                model="claude-opus-5",
                effort="max",
                max_turns=32,
                budget_usd=5.0,
                retries=1,
                task="Challenge the master decision.",
            ),
        )
        calls: list[tuple[str, int]] = []

        async def fake_execute_agent(**kwargs):
            agent = kwargs["agent"]
            calls.append((agent.model, kwargs["attempt"]))
            payload = fake_payload(agent, kwargs["attempt"], success=True, cost=0.25)
            atomic_json(kwargs["output_path"], payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "resumable-source-run"
            source_plan = LaunchPlan(
                agents=(source_agent,),
                task="Synthetic source task",
                concurrency=1,
                per_agent_budget_usd=0.5,
                retries=0,
                aggregate_ceiling_usd=0.5,
                run_dir=run_dir,
            )
            atomic_json(
                run_dir / "results" / f"{source_agent.name}.json",
                fake_payload(source_agent, 1, success=True, cost=0.1),
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                first = await launch_post_source_pipeline(
                    root=WORKSPACE,
                    source_plan=source_plan,
                    source_summary={"status": "complete", "failed": 0, "deferred": 0},
                    stages=stages,
                    agents=agents,
                )
                second = await launch_post_source_pipeline(
                    root=WORKSPACE,
                    source_plan=source_plan,
                    source_summary={"status": "complete", "failed": 0, "deferred": 0},
                    stages=stages,
                    agents=agents,
                )

            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "complete")
            self.assertEqual(calls, [("claude-fable-5", 1), ("claude-opus-5", 1)])
            for record in second["stages"]:
                child = run_dir.parent / record["run_id"]
                manifest = json.loads((child / "manifest.json").read_text())
                self.assertEqual(len(manifest["launch_history"]), 2)

            atomic_json(
                run_dir / "results" / f"{source_agent.name}.json",
                fake_payload(source_agent, 2, success=True, cost=0.2),
            )
            with self.assertRaisesRegex(ValueError, "source corpus.*changed"):
                await launch_post_source_pipeline(
                    root=WORKSPACE,
                    source_plan=source_plan,
                    source_summary={"status": "complete", "failed": 0, "deferred": 0},
                    stages=stages,
                    agents=agents,
                )

    def test_post_source_pipeline_ceiling_includes_repair_attempts(self):
        stages = (
            PostSourceStage(
                name="frontier-synthesis",
                agent_name="atlas-synthesis-decision-memo",
                model="claude-fable-5",
                effort="max",
                max_turns=36,
                budget_usd=10.0,
                retries=1,
                task="Synthesize.",
            ),
            PostSourceStage(
                name="opus-challenge",
                agent_name="atlas-council-adversarial-review",
                model="claude-opus-5",
                effort="max",
                max_turns=32,
                budget_usd=5.0,
                retries=2,
                task="Challenge.",
            ),
        )
        self.assertEqual(post_source_pipeline_ceiling(stages), 35.0)

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

    def test_global_provider_failure_classification_is_narrow(self):
        agent = next(iter(load_agents(WORKSPACE)))
        cases = (
            ("You've hit your Claude usage limit. Your limit resets at 3 PM.", "session-limit"),
            ("Your organization quota has been exhausted.", "account-quota"),
            ("authentication_error: invalid x-api-key", "authentication"),
        )
        for error, expected_code in cases:
            with self.subTest(error=error):
                failure = classify_global_provider_failure(global_error_payload(agent, 1, error))
                self.assertIsNotNone(failure)
                self.assertEqual(failure["code"], expected_code)

        retryable = global_error_payload(
            agent,
            1,
            "rate_limit_error: this session received HTTP 429; retry after 2 seconds",
        )
        self.assertIsNone(classify_global_provider_failure(retryable))

        result_message_error = global_error_payload(agent, 1, "placeholder")
        result_message_error["result"]["errors"] = []
        result_message_error["result"]["error_message"] = "You've hit your limit; resets at 4 PM."
        self.assertEqual(
            classify_global_provider_failure(result_message_error)["code"],
            "session-limit",
        )

    def test_session_limit_wait_uses_relative_clock_and_fallback_hints(self):
        now = datetime(2026, 8, 29, 19, 0, tzinfo=timezone.utc)
        relative = {
            "error": "You've hit your limit; try again in 90 minutes.",
            "reset_hint": "try again in 90 minutes",
        }
        clock = {
            "error": "You've hit your limit; resets at 3 PM (America/Los_Angeles).",
            "reset_hint": "resets at 3 PM (America/Los_Angeles)",
        }
        unknown = {
            "error": "Session limit reached.",
            "reset_hint": "Resume after the Claude session limit resets.",
        }
        distant = {
            "error": "You've hit your limit; try again in 999 hours.",
            "reset_hint": "try again in 999 hours",
        }
        malformed_clock = {
            "error": "You've hit your limit; resets at 99:99 PM.",
            "reset_hint": "resets at 99:99 PM",
        }

        self.assertEqual(session_limit_wait_seconds(relative, now=now), 5405.0)
        self.assertEqual(session_limit_wait_seconds(clock, now=now), 10805.0)
        self.assertEqual(session_limit_wait_seconds(unknown, now=now), 300.0)
        self.assertEqual(session_limit_wait_seconds(distant, now=now), 21600.0)
        self.assertEqual(session_limit_wait_seconds(malformed_clock, now=now), 300.0)

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
            self.assertEqual(first["session_reset_count"], 0)
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

    async def test_global_limit_pauses_queued_work_and_same_run_resumes(self):
        agents_by_name = {agent.name: agent for agent in load_agents(WORKSPACE)}
        completed_agent = agents_by_name["atlas-research-quality-falsification-agent"]
        limited_agent = agents_by_name["atlas-research-quality-verifier-auditor"]
        deferred_agent = agents_by_name["atlas-council-adversarial-review"]
        agents = (completed_agent, limited_agent, deferred_agent)
        calls: list[tuple[str, int, str]] = []
        phase = "pause"

        async def fake_execute_agent(**kwargs):
            agent = kwargs["agent"]
            attempt = kwargs["attempt"]
            calls.append((agent.name, attempt, phase))
            if phase == "pause" and agent.name == limited_agent.name:
                payload = global_error_payload(
                    agent,
                    attempt,
                    "Your organization quota has been exhausted. Quota resets at 3 PM.",
                )
            else:
                payload = fake_payload(agent, attempt, success=True, cost=0.10)
            atomic_json(kwargs["output_path"], payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "global-pause-run"
            plan = LaunchPlan(
                agents=agents, task="Synthetic global pause test", concurrency=1,
                per_agent_budget_usd=0.50, retries=3, aggregate_ceiling_usd=6.0,
                run_dir=run_dir,
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                first = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

                self.assertEqual(first["status"], "paused")
                self.assertEqual(first["run_id"], run_dir.name)
                self.assertEqual(first["succeeded"], 1)
                self.assertEqual(first["failed"], 1)
                self.assertEqual(first["deferred"], 1)
                self.assertEqual(first["completed_agents"], [completed_agent.name])
                self.assertEqual(first["failed_agents"], [limited_agent.name])
                self.assertEqual(first["deferred_agents"], [deferred_agent.name])
                self.assertEqual(
                    [(name, attempt) for name, attempt, _ in calls],
                    [(completed_agent.name, 1), (limited_agent.name, 1)],
                )
                self.assertTrue((run_dir / "results" / f"{completed_agent.name}.json").is_file())
                self.assertTrue((run_dir / "results" / f"{limited_agent.name}.json").is_file())
                self.assertFalse((run_dir / "results" / f"{deferred_agent.name}.json").exists())

                pause = json.loads((run_dir / "pause.json").read_text())
                progress = json.loads((run_dir / "progress.json").read_text())
                self.assertEqual(pause["code"], "account-quota")
                self.assertIn("organization quota", pause["error"])
                self.assertIn("resets at 3 PM", pause["reset_hint"])
                self.assertEqual(pause["resume_run_id"], run_dir.name)
                self.assertEqual(progress["status"], "paused")
                self.assertEqual(progress["completed_agents"], [completed_agent.name])
                self.assertEqual(progress["failed_agents"], [limited_agent.name])
                self.assertEqual(progress["deferred_agents"], [deferred_agent.name])

                phase = "resume"
                second = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(second["status"], "complete")
            self.assertEqual(second["run_id"], first["run_id"])
            self.assertEqual(second["succeeded"], 3)
            self.assertEqual(second["failed"], 0)
            self.assertEqual(second["deferred"], 0)
            self.assertEqual(second["skipped_successful"], 1)
            self.assertEqual(
                [(name, attempt) for name, attempt, _ in calls],
                [
                    (completed_agent.name, 1),
                    (limited_agent.name, 1),
                    (limited_agent.name, 2),
                    (deferred_agent.name, 1),
                ],
            )
            self.assertEqual(json.loads((run_dir / "pause.json").read_text())["status"], "resumed")
            self.assertEqual(len(json.loads((run_dir / "manifest.json").read_text())["launch_history"]), 2)

    async def test_session_limit_waits_and_resumes_without_a_second_launch(self):
        agents_by_name = {agent.name: agent for agent in load_agents(WORKSPACE)}
        completed_agent = agents_by_name["atlas-research-quality-falsification-agent"]
        limited_agent = agents_by_name["atlas-research-quality-verifier-auditor"]
        deferred_agent = agents_by_name["atlas-council-adversarial-review"]
        calls: list[tuple[str, int]] = []
        waiting_snapshots: list[tuple[dict, dict, dict]] = []

        async def fake_execute_agent(**kwargs):
            agent = kwargs["agent"]
            attempt = kwargs["attempt"]
            calls.append((agent.name, attempt))
            if agent.name == limited_agent.name and attempt == 1:
                payload = global_error_payload(
                    agent,
                    attempt,
                    "You've hit your Claude usage limit. Your limit resets at 3 PM.",
                )
            else:
                payload = fake_payload(agent, attempt, success=True, cost=0.10)
            atomic_json(kwargs["output_path"], payload)
            return payload

        async def fake_wait(_seconds):
            waiting_snapshots.append((
                json.loads((run_dir / "progress.json").read_text()),
                json.loads((run_dir / "summary.json").read_text()),
                json.loads((run_dir / "pause.json").read_text()),
            ))

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "automatic-session-resume-run"
            plan = LaunchPlan(
                agents=(completed_agent, limited_agent, deferred_agent),
                task="Synthetic automatic session resume test", concurrency=1,
                per_agent_budget_usd=0.50, retries=2, aggregate_ceiling_usd=4.5,
                run_dir=run_dir,
            )
            with (
                patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent),
                patch("wizengamot.runner.session_limit_wait_seconds", return_value=42.0),
                patch("wizengamot.runner.wait_for_session_reset", new=fake_wait),
            ):
                summary = await launch_plan(
                    root=WORKSPACE,
                    plan=plan,
                    campaign_name=None,
                    campaign_prompt=None,
                    skip_existing=False,
                )

            self.assertEqual(
                calls,
                [
                    (completed_agent.name, 1),
                    (limited_agent.name, 1),
                    (limited_agent.name, 2),
                    (deferred_agent.name, 1),
                ],
            )
            self.assertEqual(len(waiting_snapshots), 1)
            waiting_progress, waiting_summary, waiting_pause = waiting_snapshots[0]
            for record in (waiting_progress, waiting_summary, waiting_pause):
                self.assertEqual(record["status"], "waiting-for-session-reset")
                self.assertEqual(record["session_reset_count"], 1)
            self.assertEqual(waiting_summary["completed_agents"], [completed_agent.name])
            self.assertEqual(waiting_summary["failed_agents"], [limited_agent.name])
            self.assertEqual(waiting_summary["deferred_agents"], [deferred_agent.name])
            self.assertEqual(waiting_pause["wait_seconds"], 42.0)

            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["succeeded"], 3)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(summary["deferred"], 0)
            self.assertEqual(summary["attempt_count"], 4)
            self.assertEqual(summary["session_reset_count"], 1)
            self.assertEqual(summary["session_limit_wait_count"], 1)
            self.assertEqual(summary["skipped_successful"], 1)
            self.assertEqual(json.loads((run_dir / "pause.json").read_text())["status"], "resumed")
            waits = json.loads((run_dir / "session-waits.json").read_text())
            self.assertEqual(waits["session_reset_count"], 1)
            self.assertEqual(waits["waits"][0]["wait_seconds"], 42.0)
            self.assertEqual(len(json.loads((run_dir / "manifest.json").read_text())["launch_history"]), 1)
            self.assertTrue((run_dir / "results" / f"{deferred_agent.name}.json").is_file())

    async def test_repeated_session_limits_reenter_wait_until_success(self):
        agent = next(
            agent for agent in load_agents(WORKSPACE)
            if agent.name == "atlas-research-quality-verifier-auditor"
        )
        calls: list[int] = []
        waits: list[float] = []

        async def fake_execute_agent(**kwargs):
            attempt = kwargs["attempt"]
            calls.append(attempt)
            if attempt < 3:
                payload = global_error_payload(
                    agent,
                    attempt,
                    "You've hit your Claude usage limit; resets in 1 minute.",
                )
            else:
                payload = fake_payload(agent, attempt, success=True, cost=0.10)
            atomic_json(kwargs["output_path"], payload)
            return payload

        async def fake_wait(seconds):
            waits.append(seconds)

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "repeated-session-wait-run"
            plan = LaunchPlan(
                agents=(agent,), task="Synthetic repeated session wait test", concurrency=1,
                per_agent_budget_usd=0.50, retries=0, aggregate_ceiling_usd=0.50,
                run_dir=run_dir,
            )
            with (
                patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent),
                patch("wizengamot.runner.session_limit_wait_seconds", return_value=7.0),
                patch("wizengamot.runner.wait_for_session_reset", new=fake_wait),
            ):
                summary = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(calls, [1, 2, 3])
            self.assertEqual(waits, [7.0, 7.0])
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["session_reset_count"], 2)
            self.assertEqual(summary["session_limit_wait_count"], 2)
            self.assertEqual(len(json.loads((run_dir / "session-waits.json").read_text())["waits"]), 2)
            self.assertEqual(len(json.loads((run_dir / "manifest.json").read_text())["launch_history"]), 1)

    async def test_session_recovery_does_not_replenish_ordinary_retry_budget(self):
        agents_by_name = {agent.name: agent for agent in load_agents(WORKSPACE)}
        failing_agent = agents_by_name["atlas-research-quality-falsification-agent"]
        limited_agent = agents_by_name["atlas-research-quality-verifier-auditor"]
        calls: list[tuple[str, int]] = []

        async def fake_execute_agent(**kwargs):
            agent = kwargs["agent"]
            attempt = kwargs["attempt"]
            calls.append((agent.name, attempt))
            if agent.name == failing_agent.name:
                payload = fake_payload(agent, attempt, success=False, cost=0.10)
            elif attempt == 1:
                payload = global_error_payload(
                    agent,
                    attempt,
                    "You've hit your Claude usage limit; resets in 1 minute.",
                )
            else:
                payload = fake_payload(agent, attempt, success=True, cost=0.10)
            atomic_json(kwargs["output_path"], payload)
            return payload

        async def fake_wait(_seconds):
            return None

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "preserved-retry-budget-run"
            plan = LaunchPlan(
                agents=(failing_agent, limited_agent),
                task="Synthetic preserved retry budget test", concurrency=1,
                per_agent_budget_usd=0.50, retries=1, aggregate_ceiling_usd=2.0,
                run_dir=run_dir,
            )
            with (
                patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent),
                patch("wizengamot.runner.session_limit_wait_seconds", return_value=7.0),
                patch("wizengamot.runner.wait_for_session_reset", new=fake_wait),
            ):
                summary = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(
                calls,
                [
                    (failing_agent.name, 1),
                    (failing_agent.name, 2),
                    (limited_agent.name, 1),
                    (limited_agent.name, 2),
                ],
            )
            self.assertEqual(summary["completed_agents"], [limited_agent.name])
            self.assertEqual(summary["failed_agents"], [failing_agent.name])
            self.assertEqual(summary["session_reset_count"], 1)
            self.assertEqual(summary["session_limit_wait_count"], 1)

    async def test_global_account_quota_drains_an_already_active_worker(self):
        agents_by_name = {agent.name: agent for agent in load_agents(WORKSPACE)}
        limited_agent = agents_by_name["atlas-research-quality-verifier-auditor"]
        draining_agent = agents_by_name["atlas-research-quality-falsification-agent"]
        deferred_agent = agents_by_name["atlas-council-adversarial-review"]
        active_started = asyncio.Event()
        calls: list[str] = []

        async def fake_execute_agent(**kwargs):
            agent = kwargs["agent"]
            calls.append(agent.name)
            if agent.name == limited_agent.name:
                await active_started.wait()
                payload = global_error_payload(
                    agent,
                    kwargs["attempt"],
                    "Your organization quota has been exhausted.",
                )
            else:
                active_started.set()
                await asyncio.sleep(0.01)
                payload = fake_payload(agent, kwargs["attempt"], success=True, cost=0.10)
            atomic_json(kwargs["output_path"], payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "active-drain-run"
            plan = LaunchPlan(
                agents=(limited_agent, draining_agent, deferred_agent),
                task="Synthetic active drain test", concurrency=2,
                per_agent_budget_usd=0.50, retries=2, aggregate_ceiling_usd=4.5,
                run_dir=run_dir,
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                summary = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(set(calls), {limited_agent.name, draining_agent.name})
            self.assertEqual(summary["completed_agents"], [draining_agent.name])
            self.assertEqual(summary["failed_agents"], [limited_agent.name])
            self.assertEqual(summary["deferred_agents"], [deferred_agent.name])
            self.assertTrue((run_dir / "results" / f"{draining_agent.name}.json").is_file())
            self.assertFalse((run_dir / "results" / f"{deferred_agent.name}.json").exists())

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

    async def test_resumed_contract_error_gets_saved_feedback_on_first_new_attempt(self):
        agent = next(
            a for a in load_agents(WORKSPACE)
            if a.name == "atlas-research-quality-verifier-auditor"
        )
        calls: list[tuple[int, list[str] | None]] = []

        async def fake_execute_agent(**kwargs):
            attempt = kwargs["attempt"]
            calls.append((attempt, kwargs["prior_report_contract_errors"]))
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
            run_dir = Path(td) / "contract-resume-feedback-run"
            plan = LaunchPlan(
                agents=(agent,), task="Synthetic contract resume feedback test", concurrency=1,
                per_agent_budget_usd=0.50, retries=0, aggregate_ceiling_usd=0.50,
                run_dir=run_dir,
            )
            with patch("wizengamot.runner.execute_agent", side_effect=fake_execute_agent):
                first = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )
                saved = json.loads(
                    (run_dir / "results" / f"{agent.name}.json").read_text()
                )["report_contract_errors"]
                second = await launch_plan(
                    root=WORKSPACE, plan=plan, campaign_name=None, campaign_prompt=None,
                )

            self.assertEqual(first["failed"], 1)
            self.assertEqual(second["succeeded"], 1)
            self.assertEqual(calls[0], (1, None))
            self.assertEqual(calls[1], (2, saved))
            self.assertTrue(calls[1][1])

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
