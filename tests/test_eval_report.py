from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import eval_report  # noqa: E402


class EvalReportTests(unittest.TestCase):
    def test_bundled_samples_pass(self) -> None:
        for case_path in sorted((ROOT / "evals" / "cases").glob("*.json")):
            with self.subTest(case=case_path.name):
                case = eval_report.load_case(case_path)
                result = eval_report.evaluate_text(case["sample_output"], case)
                self.assertTrue(result["passed"], result["findings"])

    def test_unknown_evidence_strengthened_claim_and_pii_fail(self) -> None:
        case = {
            "case_id": "failure-surface",
            "expected": {
                "allowed_evidence_ids": ["CV-01"],
                "required_evidence_ids": ["CV-01"],
                "required_phrases": [],
                "forbidden_phrases": ["led the migration"],
                "max_unresolved_confirmations": 0,
            },
            "private_test_values": [],
        }
        result = eval_report.evaluate_text(
            "[Resume Fact | CV-01] I led the migration. JD-99. Contact me at test@example.com.", case
        )
        codes = result["counts"]
        self.assertFalse(result["passed"])
        self.assertEqual(codes["unknown_evidence"], 1)
        self.assertEqual(codes["forbidden_phrase"], 1)
        self.assertEqual(codes["pii_leak"], 1)

    def test_html_visible_text_excludes_scripts_and_styles(self) -> None:
        raw = "<html><style>.x{content:'CV-99'}</style><body>CV-01</body><script>'JD-88'</script></html>"
        self.assertEqual(eval_report.visible_text(raw, ".html"), "CV-01")

    def test_case_schema_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.json"
            path.write_text(json.dumps({"schema_version": 2, "case_id": "x"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                eval_report.load_case(path)


if __name__ == "__main__":
    unittest.main()
