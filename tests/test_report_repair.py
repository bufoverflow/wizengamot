from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from wizengamot.report_contract import GENERIC_SOURCE_ID_RE, validate_report_contract
from wizengamot.report_repair import plan_payload_repairs, repair_run_contract


def citation(
    source_id: str = "ext-example-guide",
    *,
    locator: str | None = "https://example.com/guide",
    source: str = "Example guide",
    publisher: str | None = "Example Publisher",
    source_class: str = "external-secondary",
    claims: list[str] | None = None,
) -> dict:
    return {
        "source_id": source_id,
        "label": "Example guide",
        "source_class": source_class,
        "publisher": publisher,
        "source": source,
        "locator": locator,
        "date": "2026-08-30",
        "primary": source_class == "external-primary",
        "claims_supported": claims or ["Example claim"],
    }


def finding(**overrides) -> dict:
    value = {
        "statement": "Example claim",
        "classification": "fact",
        "evidence_class": "external-secondary",
        "novelty": "retrieved",
        "claim_type": "positive",
        "source_ids": ["ext-example-guide"],
        "reviewed_source_ids": [],
        "confidence": "moderate",
        "impact": "Exercises deterministic repair.",
        "evidence": ["Example evidence"],
    }
    value.update(overrides)
    return value


def payload(*, citations: list[dict] | None = None, findings: list[dict] | None = None) -> dict:
    report = {
        "agent_name": "example-agent",
        "status": "complete",
        "findings": findings if findings is not None else [finding()],
        "citations": citations if citations is not None else [citation()],
    }
    return {
        "agent": {"name": "example-agent"},
        "attempt": 1,
        "result": {"subtype": "success", "is_error": False, "report": report},
        "report_contract_errors": validate_report_contract(report),
    }


class ReportRepairTests(unittest.TestCase):
    def test_markdown_url_is_normalized(self):
        value = payload(citations=[citation(locator="[https://example.com/guide](https://example.com/guide)")])

        decision = plan_payload_repairs(value, timestamp="2026-08-30T00:00:00+00:00")

        self.assertTrue(decision["accepted"])
        repaired = decision["payload"]["result"]["report"]
        self.assertEqual(repaired["citations"][0]["locator"], "https://example.com/guide")

    def test_source_url_fills_missing_locator(self):
        for source, expected in (
            ("https://example.com/direct", "https://example.com/direct"),
            ("doi:10.1234/EXAMPLE.5", "doi:10.1234/example.5"),
        ):
            with self.subTest(source=source):
                value = payload(citations=[citation(locator=None, source=source)])

                decision = plan_payload_repairs(value)

                self.assertTrue(decision["accepted"])
                repaired = decision["payload"]["result"]["report"]
                self.assertEqual(repaired["citations"][0]["locator"], expected)

    def test_exactly_one_embedded_url_is_extracted_but_placeholder_is_not_invented(self):
        embedded = payload(citations=[citation(
            locator="Retrieved from https://example.com/direct on 2026-08-30",
        )])
        placeholder = payload(citations=[citation(locator="article body")])

        embedded_decision = plan_payload_repairs(embedded)
        placeholder_decision = plan_payload_repairs(placeholder)

        self.assertTrue(embedded_decision["accepted"])
        self.assertEqual(
            embedded_decision["payload"]["result"]["report"]["citations"][0]["locator"],
            "https://example.com/direct",
        )
        self.assertFalse(placeholder_decision["accepted"])
        self.assertEqual(
            placeholder_decision["payload"]["result"]["report"]["citations"][0]["locator"],
            "article body",
        )

    def test_generic_id_is_deterministic_and_all_references_are_updated(self):
        value = payload(
            citations=[citation("C1", source="Issuing spending controls")],
            findings=[finding(
                source_ids=["C1"],
                reviewed_source_ids=["C1"],
                claim_type="comparative",
            )],
        )

        first = plan_payload_repairs(value, timestamp="2026-08-30T00:00:00+00:00")
        second = plan_payload_repairs(value, timestamp="2026-08-31T00:00:00+00:00")

        self.assertTrue(first["accepted"])
        first_report = first["payload"]["result"]["report"]
        second_report = second["payload"]["result"]["report"]
        source_id = first_report["citations"][0]["source_id"]
        self.assertEqual(source_id, second_report["citations"][0]["source_id"])
        self.assertRegex(source_id, r"^ext-example-publisher-issuing-spending-controls-[0-9a-f]{8}$")
        self.assertEqual(first_report["findings"][0]["source_ids"], [source_id])
        self.assertEqual(first_report["findings"][0]["reviewed_source_ids"], [source_id])

    def test_stable_source_id_collision_rejects_the_report(self):
        generic = payload(
            citations=[citation("C1", source="Issuing spending controls")],
            findings=[finding(source_ids=["C1"])],
        )
        target_id = plan_payload_repairs(generic)["payload"]["result"]["report"]["citations"][0]["source_id"]
        colliding = payload(
            citations=[
                citation("C1", source="Issuing spending controls"),
                citation(target_id, locator="https://example.com/other", source="Other guide"),
            ],
            findings=[finding(source_ids=["C1"])],
        )

        decision = plan_payload_repairs(colliding)

        self.assertFalse(decision["accepted"])
        self.assertIn("collides", decision["reason"])

    def test_duplicate_direct_url_citations_are_merged(self):
        value = payload(
            citations=[
                citation("ext-example-one", claims=["Claim one"]),
                citation("ext-example-two", claims=["Claim two"]),
            ],
            findings=[finding(
                source_ids=["ext-example-one", "ext-example-two"],
                novelty="corroborated",
            )],
        )

        decision = plan_payload_repairs(value)

        self.assertTrue(decision["accepted"])
        report = decision["payload"]["result"]["report"]
        self.assertEqual(len(report["citations"]), 1)
        self.assertEqual(report["citations"][0]["claims_supported"], ["Claim one", "Claim two"])
        self.assertEqual(len(report["findings"][0]["source_ids"]), 1)
        self.assertEqual(report["findings"][0]["novelty"], "derived")

    def test_duplicate_merge_preserves_strongest_coherent_metadata(self):
        value = payload(
            citations=[
                citation("ext-example-secondary", source_class="external-secondary"),
                citation("C1", source_class="external-primary"),
            ],
            findings=[finding(source_ids=["ext-example-secondary", "C1"])],
        )

        decision = plan_payload_repairs(value)

        self.assertTrue(decision["accepted"])
        retained = decision["payload"]["result"]["report"]["citations"][0]
        self.assertEqual(retained["source_class"], "external-primary")
        self.assertIs(retained["primary"], True)
        self.assertFalse(GENERIC_SOURCE_ID_RE.fullmatch(retained["source_id"]))

    def test_non_identifying_duplicate_locators_are_not_merged(self):
        value = payload(
            citations=[
                citation("ext-example-one", locator="article"),
                citation("ext-example-two", locator="article"),
            ],
            findings=[],
        )

        decision = plan_payload_repairs(value)

        self.assertFalse(decision["accepted"])
        self.assertEqual(len(decision["payload"]["result"]["report"]["citations"]), 2)

    def test_one_source_corroboration_is_downgraded(self):
        value = payload(findings=[finding(novelty="corroborated")])

        decision = plan_payload_repairs(value)

        self.assertTrue(decision["accepted"])
        self.assertEqual(decision["payload"]["result"]["report"]["findings"][0]["novelty"], "derived")

    def test_reviewed_sources_inherit_only_existing_source_ids(self):
        value = payload(findings=[finding(
            claim_type="negative-capability",
            source_ids=["ext-example-guide", "ext-example-guide"],
            reviewed_source_ids=[],
        )])

        decision = plan_payload_repairs(value)

        self.assertTrue(decision["accepted"])
        finding_value = decision["payload"]["result"]["report"]["findings"][0]
        self.assertEqual(finding_value["reviewed_source_ids"], ["ext-example-guide"])

    def test_evidence_quality_is_only_downgraded(self):
        secondary = payload(findings=[finding(evidence_class="external-primary")])
        reasoning = payload(citations=[], findings=[finding(
            evidence_class="external-primary",
            source_ids=[],
        )])

        secondary_decision = plan_payload_repairs(secondary)
        reasoning_decision = plan_payload_repairs(reasoning)

        self.assertEqual(
            secondary_decision["payload"]["result"]["report"]["findings"][0]["evidence_class"],
            "external-secondary",
        )
        reasoning_finding = reasoning_decision["payload"]["result"]["report"]["findings"][0]
        self.assertEqual(reasoning_finding["evidence_class"], "model-reasoning")
        self.assertEqual(reasoning_finding["classification"], "inference")

    def test_dry_run_changes_no_files(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            result_path = run_dir / "results/example-agent.json"
            attempt_path = run_dir / "attempts/example-agent/attempt-1.json"
            result_path.parent.mkdir(parents=True)
            attempt_path.parent.mkdir(parents=True)
            value = payload(findings=[finding(novelty="corroborated")])
            encoded = json.dumps(value, indent=2).encode()
            result_path.write_bytes(encoded)
            attempt_path.write_bytes(encoded)

            summary = repair_run_contract(run_dir, apply=False)

            self.assertEqual(result_path.read_bytes(), encoded)
            self.assertEqual(attempt_path.read_bytes(), encoded)
            self.assertFalse((run_dir / "report-contract-repair-summary.json").exists())
            self.assertFalse((run_dir / "report-contract-backups").exists())
            self.assertEqual(summary["repaired_reports"], 1)

    def test_apply_writes_backup_audit_and_never_changes_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            result_path = run_dir / "results/example-agent.json"
            attempt_path = run_dir / "attempts/example-agent/attempt-1.json"
            result_path.parent.mkdir(parents=True)
            attempt_path.parent.mkdir(parents=True)
            value = payload(findings=[finding(novelty="corroborated")])
            encoded = json.dumps(value, indent=2).encode()
            result_path.write_bytes(encoded)
            attempt_path.write_bytes(encoded)

            summary = repair_run_contract(
                run_dir,
                apply=True,
                timestamp="2026-08-30T00:00:00+00:00",
            )

            repaired = json.loads(result_path.read_text())
            audit = repaired["report_contract_repairs"][0]
            self.assertEqual(
                set(audit),
                {"field_path", "old_value", "new_value", "rule", "timestamp", "tool_version"},
            )
            backup = Path(summary["backup_directory"]) / "results/example-agent.json"
            self.assertEqual(backup.read_bytes(), encoded)
            self.assertEqual(attempt_path.read_bytes(), encoded)
            self.assertTrue((run_dir / "report-contract-repair-summary.json").is_file())

    def test_rejects_formatting_change_when_error_set_is_equivalent(self):
        value = payload(citations=[citation(
            locator="HTTPS://Example.com/guide",
            publisher=None,
        )])
        original = copy.deepcopy(value)

        decision = plan_payload_repairs(value)

        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["payload"], original)
        self.assertIn("not strictly smaller", decision["reason"])

    def test_apply_invalidates_cached_source_corpus_hash(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            result_path = run_dir / "results/example-agent.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text(json.dumps(payload(findings=[finding(novelty="corroborated")])))
            (run_dir / "source-corpus-index.json").write_text(json.dumps({
                "source_corpus_sha256": "old-hash",
            }))
            (run_dir / "post-source-pipeline.json").write_text(json.dumps({
                "status": "blocked-source-contract",
                "definition": {"source_corpus_sha256": "old-hash"},
                "source_contract": {"source_corpus_sha256": "old-hash"},
                "stages": [],
            }))
            (run_dir / "summary.json").write_text(json.dumps({
                "post_source_pipeline": {
                    "status": "blocked-source-contract",
                    "definition": {"source_corpus_sha256": "old-hash"},
                    "source_contract": {"source_corpus_sha256": "old-hash"},
                    "stages": [],
                },
            }))

            summary = repair_run_contract(
                run_dir,
                apply=True,
                timestamp="2026-08-30T00:00:00+00:00",
            )

            self.assertFalse((run_dir / "source-corpus-index.json").exists())
            pipeline = json.loads((run_dir / "post-source-pipeline.json").read_text())
            self.assertIsNone(pipeline["definition"]["source_corpus_sha256"])
            self.assertIsNone(pipeline["source_contract"])
            saved_summary = json.loads((run_dir / "summary.json").read_text())
            self.assertIsNone(
                saved_summary["post_source_pipeline"]["definition"]["source_corpus_sha256"]
            )
            self.assertIsNone(saved_summary["post_source_pipeline"]["source_contract"])
            self.assertEqual(summary["source_corpus_cache"]["status"], "invalidated")


if __name__ == "__main__":
    unittest.main()
