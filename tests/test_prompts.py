from __future__ import annotations

import json
import unittest
from pathlib import Path

from wizengamot.prompts import load_output_schema

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


if __name__ == "__main__":
    unittest.main()
