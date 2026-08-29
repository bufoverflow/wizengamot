from __future__ import annotations

import copy
import unittest

from wizengamot.report_contract import (
    annotate_report_contract,
    payload_contract_errors,
    validate_report_contract,
)
from wizengamot.runner import payload_report_status, payload_succeeded


def citation(
    source_id: str,
    *,
    source_class: str = "external-primary",
    locator: str | None = None,
) -> dict:
    primary = source_class == "external-primary"
    return {
        "source_id": source_id,
        "label": source_id,
        "source_class": source_class,
        "publisher": "Example Publisher",
        "source": "Example source",
        "locator": locator or f"https://example.com/{source_id}",
        "date": "2026-08-29",
        "primary": primary,
        "claims_supported": ["Example claim"],
    }


def finding(**overrides) -> dict:
    value = {
        "statement": "Example finding",
        "classification": "fact",
        "evidence_class": "external-primary",
        "novelty": "retrieved",
        "claim_type": "positive",
        "source_ids": ["ext-example-primary"],
        "reviewed_source_ids": [],
        "confidence": "high",
        "impact": "Example impact",
        "evidence": ["Example evidence"],
    }
    value.update(overrides)
    return value


def report() -> dict:
    return {
        "agent_name": "example-agent",
        "status": "complete",
        "findings": [finding()],
        "citations": [citation("ext-example-primary")],
    }


class ReportContractTests(unittest.TestCase):
    def test_valid_report_has_no_contract_errors(self):
        self.assertEqual(validate_report_contract(report()), [])

    def test_external_citation_metadata_and_stable_id_are_enforced(self):
        value = report()
        value["findings"][0]["source_ids"] = ["S1"]
        value["citations"][0].update({
            "source_id": "S1",
            "publisher": " ",
            "locator": None,
            "primary": False,
            "claims_supported": [],
        })

        errors = validate_report_contract(value)

        self.assertTrue(any("is generic" in error for error in errors), errors)
        self.assertTrue(any("requires a publisher" in error for error in errors), errors)
        self.assertTrue(any("requires a locator" in error for error in errors), errors)
        self.assertTrue(any("must set primary=true" in error for error in errors), errors)
        self.assertTrue(any("requires at least one claim" in error for error in errors), errors)

    def test_report_local_citation_sequences_are_not_descriptive_ids(self):
        for source_id in ("C1", "C13a", "ref_4", "ext-2"):
            with self.subTest(source_id=source_id):
                value = report()
                value["citations"][0]["source_id"] = source_id
                value["findings"][0]["source_ids"] = [source_id]

                errors = validate_report_contract(value)

                self.assertTrue(any("is generic" in error for error in errors), errors)

    def test_external_secondary_requires_primary_false(self):
        value = report()
        value["citations"][0]["source_class"] = "external-secondary"
        value["citations"][0]["primary"] = True
        value["findings"] = []

        errors = validate_report_contract(value)

        self.assertTrue(any("must set primary=false" in error for error in errors), errors)

    def test_external_citation_rejects_bundles_and_placeholder_locators(self):
        value = report()
        value["findings"] = []
        value["citations"][0].update({
            "publisher": "unspecified industry coverage",
            "source": "multiple secondary articles",
            "locator": "search-result summary of article body",
        })

        errors = validate_report_contract(value)

        self.assertTrue(any("identify one publisher" in error for error in errors), errors)
        self.assertTrue(any("identify exactly one source" in error for error in errors), errors)
        self.assertTrue(any("direct URL, DOI" in error for error in errors), errors)

        value = report()
        value["findings"] = []
        value["citations"][0]["source"] = "web"
        errors = validate_report_contract(value)
        self.assertTrue(any("identify exactly one source" in error for error in errors), errors)

    def test_external_citation_accepts_url_doi_and_formal_locators(self):
        for locator in (
            "https://example.com/official-guide.pdf",
            "doi:10.1234/example.5678",
            "§ 5-108(a), (e), and (f)",
            "Rules 1.06 and 1.07",
        ):
            with self.subTest(locator=locator):
                value = report()
                value["citations"][0]["locator"] = locator
                self.assertEqual(validate_report_contract(value), [])

        value = report()
        value["citations"][0].update({
            "source": "https://example.com/official-announcement",
            "locator": "official announcement dated 1 July 2007",
        })
        self.assertEqual(validate_report_contract(value), [])

        value["citations"][0]["locator"] = "article body"
        errors = validate_report_contract(value)
        self.assertTrue(any("direct URL, DOI" in error for error in errors), errors)

    def test_duplicate_source_ids_and_locators_are_rejected(self):
        value = report()
        duplicate = citation("ext-example-primary")
        duplicate["locator"] = value["citations"][0]["locator"]
        value["citations"].append(duplicate)

        errors = validate_report_contract(value)

        self.assertTrue(any("duplicate source_id" in error for error in errors), errors)
        self.assertTrue(any("duplicate locator" in error for error in errors), errors)

    def test_internal_locator_text_can_repeat_across_distinct_project_records(self):
        value = report()
        value["findings"] = []
        value["citations"] = [
            {
                **citation("project-record-one", source_class="project-record"),
                "source": "knowledge/one.md",
                "locator": "full document, lines 1-7",
            },
            {
                **citation("project-record-two", source_class="project-record"),
                "source": "knowledge/two.md",
                "locator": "full document, lines 1-7",
            },
        ]

        self.assertEqual(validate_report_contract(value), [])

    def test_negative_and_comparative_findings_require_reviewed_sources(self):
        for claim_type in ("negative-capability", "comparative"):
            with self.subTest(claim_type=claim_type):
                value = report()
                value["findings"][0]["claim_type"] = claim_type
                errors = validate_report_contract(value)
                self.assertTrue(any("requires at least one reviewed source" in error for error in errors), errors)

    def test_corroborated_findings_require_two_source_ids(self):
        value = report()
        value["findings"][0]["novelty"] = "corroborated"

        errors = validate_report_contract(value)

        self.assertTrue(any("requires at least two source IDs" in error for error in errors), errors)

    def test_external_references_must_resolve(self):
        value = report()
        value["findings"][0].update({
            "source_ids": ["ext-missing-source"],
            "reviewed_source_ids": ["ext-missing-review"],
        })

        errors = validate_report_contract(value)

        self.assertTrue(any("ext-missing-source" in error and "does not resolve" in error for error in errors), errors)
        self.assertTrue(any("ext-missing-review" in error and "does not resolve" in error for error in errors), errors)

    def test_external_primary_finding_must_reference_primary_citation(self):
        value = report()
        value["citations"][0] = citation(
            "ext-example-primary",
            source_class="external-secondary",
        )

        errors = validate_report_contract(value)

        self.assertTrue(any("must reference at least one primary citation" in error for error in errors), errors)

    def test_internal_reviewed_source_does_not_require_citation_record(self):
        value = report()
        value["findings"][0].update({
            "evidence_class": "project-record",
            "claim_type": "negative-capability",
            "source_ids": [],
            "reviewed_source_ids": ["project-knowledge-04-commitment-account"],
        })

        self.assertEqual(validate_report_contract(value), [])

    def test_contract_errors_are_saved_and_gate_success(self):
        invalid_report = report()
        invalid_report["citations"][0]["source_id"] = "S1"
        invalid_report["findings"][0]["source_ids"] = ["S1"]
        payload = {
            "result": {
                "is_error": False,
                "report": invalid_report,
            }
        }

        stored = annotate_report_contract(payload)

        self.assertEqual(payload["report_contract_errors"], stored)
        self.assertTrue(stored)
        self.assertEqual(payload_contract_errors(payload), stored)
        self.assertFalse(payload_succeeded(payload, expected_agent="example-agent"))
        self.assertEqual(payload_report_status(payload), "contract-error")

    def test_success_recomputes_contract_instead_of_trusting_saved_empty_errors(self):
        payload = {
            "report_contract_errors": [],
            "result": {
                "is_error": False,
                "report": report(),
            },
        }
        payload["result"]["report"]["citations"][0]["source_id"] = "ext-2"
        payload["result"]["report"]["findings"][0]["source_ids"] = ["ext-2"]

        self.assertTrue(payload_contract_errors(payload))
        self.assertFalse(payload_succeeded(payload, expected_agent="example-agent"))

    def test_annotation_replaces_stale_errors_after_repair(self):
        payload = {
            "report_contract_errors": ["stale"],
            "result": {"is_error": False, "report": copy.deepcopy(report())},
        }

        self.assertEqual(annotate_report_contract(payload), [])
        self.assertEqual(payload["report_contract_errors"], [])


if __name__ == "__main__":
    unittest.main()
