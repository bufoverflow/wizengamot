from __future__ import annotations

import json
import unittest
from pathlib import Path

from wizengamot.prompts import build_system_prompt, load_output_schema
from wizengamot.registry import load_agents

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "examples" / "atlas"


class PromptTests(unittest.TestCase):
    def test_output_schema_strips_dialect_marker_only_at_runtime(self):
        schema_path = ATLAS / "schemas" / "agent-result.schema.json"

        canonical = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(
            canonical.get("$schema"),
            "https://json-schema.org/draft/2020-12/schema",
        )

        output_format = load_output_schema(ATLAS)

        self.assertEqual(output_format["type"], "json_schema")
        self.assertNotIn("$schema", output_format["schema"])

        # Runtime normalization must not mutate the canonical schema on disk.
        canonical_after = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(canonical_after, canonical)

    def test_schema_requires_epistemic_action_and_source_fields(self):
        schema = load_output_schema(ATLAS)["schema"]
        required = set(schema["required"])
        self.assertTrue({"epistemic_notice", "actions", "evidence_bar_review"} <= required)

        finding = schema["properties"]["findings"]["items"]
        finding_required = set(finding["required"])
        self.assertTrue(
            {"evidence_class", "novelty", "claim_type", "source_ids", "reviewed_source_ids"}
            <= finding_required
        )
        self.assertEqual(
            set(finding["properties"]["claim_type"]["enum"]),
            {"positive", "negative-capability", "comparative", "other"},
        )

        citation = schema["properties"]["citations"]["items"]
        citation_required = set(citation["required"])
        self.assertTrue(
            {
                "source_id",
                "source_class",
                "publisher",
                "locator",
                "claims_supported",
            }
            <= citation_required
        )

    def test_system_prompt_enforces_epistemic_governance(self):
        agent = next(
            agent for agent in load_agents(ATLAS)
            if agent.name == "atlas-research-quality-falsification-agent"
        )
        prompt = build_system_prompt(ATLAS, agent)

        self.assertIn("This report is model-generated reasoning", prompt)
        self.assertIn("do not count rediscovery", prompt)
        self.assertIn("Every complete or partial report should terminate", prompt)
        self.assertIn("Match the evidence bar to reversibility", prompt)
        self.assertIn("cite relevant interview/customer records", prompt)

    def test_system_prompt_enforces_source_provenance(self):
        agent = next(
            agent for agent in load_agents(ATLAS)
            if agent.name == "atlas-research-quality-falsification-agent"
        )
        prompt = build_system_prompt(ATLAS, agent)

        self.assertIn("one citation object represents exactly one identifiable source", prompt)
        self.assertIn("prefer authoritative primary sources", prompt)
        self.assertIn("negative-capability statement", prompt)
        self.assertIn("absence from a marketing page is not evidence of absence", prompt)
        self.assertIn("use `corroborated` only when an independent source supports the same proposition", prompt)
        self.assertIn("use `derived`", prompt)


if __name__ == "__main__":
    unittest.main()
