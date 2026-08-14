from __future__ import annotations

import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from dag import Dag, DagError, NodeSpec  # noqa: E402
from guards import (  # noqa: E402
    Finding,
    GuardFailure,
    GuardResult,
    _expected_prefix,
    _label_present,
    _source_labels,
    guard_prepare_request,
    guard_report,
    result_for_command,
    run_semantic_checker,
    write_guard_result,
)
from provenance import (  # noqa: E402
    SourceIndex,
    contribution_escalated,
    numeric_conflicts,
    replay_derived_fact,
    sha256_path,
    validate_claim_shape,
)


class DagEdgeCaseTests(unittest.TestCase):
    def test_declaration_errors_cover_each_invariant(self) -> None:
        cases = (
            ((NodeSpec("a"), NodeSpec("a")), "duplicate"),
            ((NodeSpec(""),), "cannot be empty"),
            ((NodeSpec("a", guards=()),), "at least one guard"),
            ((NodeSpec("a", ("missing",)),), "Unknown dependencies"),
            ((NodeSpec("a", ("a",)),), "depend on itself"),
        )
        for nodes, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(DagError, message):
                Dag(nodes)

    def test_descendants_ready_and_description(self) -> None:
        graph = Dag(
            (
                NodeSpec("root"),
                NodeSpec("child", ("root",), ("schema", "hash"), optional=True),
                NodeSpec("leaf", ("child",), generated=True),
            )
        )
        self.assertEqual(graph.descendants("root"), {"child", "leaf"})
        self.assertEqual(graph.ready({}), ["root"])
        self.assertEqual(graph.ready({"root": "completed"}), ["child"])
        self.assertEqual(graph.ready({"root": "completed", "child": "invalidated"}), ["child"])
        self.assertEqual(graph.ready({"root": "failed"}), [])
        description = graph.describe()
        self.assertTrue(description[1]["optional"])
        self.assertTrue(description[2]["generated"])
        with self.assertRaisesRegex(DagError, "Unknown node"):
            graph.descendants("missing")


class ProvenanceEdgeCaseTests(unittest.TestCase):
    def test_source_index_blocks_and_span_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.md"
            source.write_text(
                "<!-- source: PDF-p001 -->\n```text\nfirst block\n```\n"
                "<!-- source: PDF-p002 | page=2 -->\n````text\nsecond block\n````\n",
                encoding="utf-8",
            )
            index = SourceIndex(source)
            self.assertEqual(index.span("PDF-p001", 0, 5), "first")
            self.assertEqual(index.span("PDF-p002", 0, 6), "second")
            with self.assertRaises(KeyError):
                index.span("missing", 0, 1)
            for start, end in ((-1, 1), (1, 1), ("0", 1)):
                with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                    index.span("PDF-p001", start, end)  # type: ignore[arg-type]
            with self.assertRaisesRegex(ValueError, "exceeds"):
                index.span("PDF-p001", 0, 100)

            duplicate = root / "duplicate.md"
            duplicate.write_text(
                "<!-- source: x -->\n```text\na\n```\n<!-- source: x -->\n```text\nb\n```\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                SourceIndex(duplicate)

    def test_claim_shape_reports_all_policy_requirements(self) -> None:
        codes = {code for code, _ in validate_claim_shape({})}
        self.assertTrue({"CLAIM_ID_MISSING", "CLAIM_TYPE_INVALID", "CLAIM_TEXT_MISSING"} <= codes)

        claims = (
            ({"claim_id": "1", "claim_type": "source_fact", "text": "x"}, "SOURCE_FACT_WITHOUT_EVIDENCE"),
            ({"claim_id": "2", "claim_type": "derived_fact", "text": "x"}, "DERIVED_CLAIM_WITHOUT_BASIS"),
            ({"claim_id": "3", "claim_type": "inference", "text": "x"}, "DERIVED_CLAIM_WITHOUT_BASIS"),
            ({"claim_id": "4", "claim_type": "recommendation", "text": "x"}, "RECOMMENDATION_WITHOUT_BASIS"),
            ({"claim_id": "5", "claim_type": "assumption", "text": "x"}, "ASSUMPTION_WITHOUT_SCOPE"),
            ({"claim_id": "6", "claim_type": "unknown", "text": "x"}, "UNKNOWN_WITHOUT_MISSING_FIELDS"),
        )
        for claim, expected in claims:
            with self.subTest(expected=expected):
                self.assertIn(expected, {code for code, _ in validate_claim_shape(claim)})

        invalid_lists = {
            "claim_id": "7",
            "claim_type": "knowledge",
            "text": "x",
            "evidence_refs": "bad",
            "basis_claim_ids": [1],
        }
        invalid_codes = {code for code, _ in validate_claim_shape(invalid_lists)}
        self.assertIn("EVIDENCE_REFS_INVALID", invalid_codes)
        self.assertIn("BASIS_CLAIMS_INVALID", invalid_codes)

    def test_contribution_numbers_and_formula_replay(self) -> None:
        self.assertTrue(contribution_escalated("I led the project", "I participated in the project"))
        self.assertFalse(contribution_escalated("I supported the project", "I participated in the project"))
        self.assertEqual(numeric_conflicts("Improved 20% in 3 months", "Improved 20%"), {"3"})

        valid = (
            ({"formula": {"operation": "count"}, "inputs": ["a", "b"], "value": 2}, True),
            ({"formula": {"operation": "sum"}, "inputs": [1, 2], "value": 3}, True),
            ({"formula": {"operation": "difference"}, "inputs": [10, 2, 1], "value": 7}, True),
            ({"formula": {"operation": "product"}, "inputs": [2, 3], "value": 6}, True),
            ({"formula": {"operation": "ratio"}, "inputs": [4, 2], "value": 2}, True),
            ({"formula": {"operation": "percentage"}, "inputs": [1, 2], "value": 50}, True),
            ({"formula": {"operation": "ratio", "precision": 2}, "inputs": [1, 3], "value": "0.33"}, True),
        )
        for claim, expected in valid:
            with self.subTest(claim=claim):
                self.assertEqual(replay_derived_fact(claim)[0], expected)

        invalid = (
            ({}, "schema"),
            ({"formula": {"operation": "sum", "precision": 13}, "inputs": [1], "value": 1}, "precision"),
            ({"formula": {"operation": "sum"}, "inputs": [], "value": 0}, "inputs"),
            ({"formula": {"operation": "ratio"}, "inputs": [1, 0], "value": 0}, "operation"),
            ({"formula": {"operation": "unknown"}, "inputs": [1], "value": 1}, "operation"),
            ({"formula": {"operation": "sum"}, "inputs": [object()], "value": 1}, "value"),
        )
        for claim, reason in invalid:
            with self.subTest(reason=reason):
                self.assertEqual(replay_derived_fact(claim), (False, reason))


class GuardEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.jd = self.root / "jd.md"
        self.resume = self.root / "resume.md"
        self.jd.write_text("参与项目，提升 20%。", encoding="utf-8")
        self.resume.write_text("参与支付项目。", encoding="utf-8")
        self.request = {
            "schema_version": 2,
            "inputs": {
                "jd": {"normalized_path": str(self.jd), "normalized_sha256": sha256_path(self.jd)},
                "resume": {"normalized_path": str(self.resume), "normalized_sha256": sha256_path(self.resume)},
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_guard_result_failure_serialization_and_helpers(self) -> None:
        result = GuardResult("node", None, "failed", [Finding("BAD")])
        self.assertFalse(result.passed)
        self.assertIn("BAD", str(GuardFailure(result)))
        path = self.root / "guards" / "result.json"
        write_guard_result(path, result)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["findings"][0]["code"], "BAD")

        artifact = self.root / "artifact.txt"
        artifact.write_text("x", encoding="utf-8")
        self.assertTrue(result_for_command("x", artifact, True, "NO").passed)
        self.assertEqual(result_for_command("x", artifact, False, "FAILED").findings[0].code, "FAILED")
        self.assertEqual(_expected_prefix("src:SRC-01"), "SRC-")
        self.assertEqual(_expected_prefix("resume"), "CV-")
        self.assertIsNone(_expected_prefix("bad"))
        self.assertTrue(_source_labels("src:SRC-01"))
        self.assertEqual(_source_labels("bad"), ())
        self.assertTrue(_label_present("source_fact", "[JD Fact] text"))
        self.assertTrue(_label_present("custom", "text"))
        self.assertFalse(_label_present("inference", "text"))

    def test_prepare_guard_reports_invalid_metadata_and_hashes(self) -> None:
        invalid = guard_prepare_request({"schema_version": 9, "inputs": []})
        self.assertFalse(invalid.passed)
        self.assertIn("REQUEST_SCHEMA_UNSUPPORTED", {item.code for item in invalid.findings})
        self.assertIn("REQUEST_INPUTS_MISSING", {item.code for item in invalid.findings})

        empty = self.root / "empty.md"
        empty.write_text("", encoding="utf-8")
        request = {
            "schema_version": 2,
            "inputs": {
                "jd": None,
                "resume": {"normalized_path": str(empty), "normalized_sha256": "bad"},
                "extra": "bad",
            },
        }
        result = guard_prepare_request(request)
        codes = {item.code for item in result.findings}
        self.assertIn("NORMALIZED_INPUT_MISSING", codes)
        self.assertIn("NORMALIZED_INPUT_METADATA_INVALID", codes)
        self.assertIn("NORMALIZED_INPUT_INVALID", codes)

        mismatched = json.loads(json.dumps(self.request))
        mismatched["inputs"]["jd"]["normalized_sha256"] = "bad"
        self.assertIn("NORMALIZED_HASH_MISMATCH", {item.code for item in guard_prepare_request(mismatched).findings})
        self.assertTrue(guard_prepare_request(self.request).passed)

    def test_report_guard_handles_missing_and_malformed_artifacts(self) -> None:
        report = self.root / "report.html"
        manifest = self.root / "manifest.json"
        result, _ = guard_report(report, manifest, self.request)
        self.assertEqual(result.findings[0].code, "REPORT_MISSING")

        report.write_text("<p>x</p>", encoding="utf-8")
        result, _ = guard_report(report, manifest, self.request)
        self.assertEqual(result.findings[0].code, "PROVENANCE_MANIFEST_MISSING")

        manifest.write_text("{", encoding="utf-8")
        result, _ = guard_report(report, manifest, self.request)
        self.assertEqual(result.findings[0].code, "PROVENANCE_MANIFEST_INVALID")

    def test_report_guard_collects_structural_and_evidence_findings(self) -> None:
        report = self.root / "report.html"
        report.write_text(
            '<p data-claim-id="HTML-UNKNOWN">unknown</p>'
            '<p>[JD事实] unbound</p>'
            '<p data-claim-id="CLM-1">different text</p>'
            '<p data-claim-id="CLM-2">[知识] knowledge</p>'
            '<p data-claim-id="CLM-3">[计算事实] computed</p>'
            '<p data-claim-id="CLM-4">[推断] inference</p>'
            '<span data-claim-id="SELF-CLOSING"/>',
            encoding="utf-8",
        )
        claims: list[object] = [
            "bad",
            {
                "claim_id": "CLM-1",
                "claim_type": "source_fact",
                "text": "expected text 30%",
                "basis_claim_ids": ["MISSING"],
                "evidence_refs": [
                    "bad",
                    {"evidence_id": "bad", "source": "invalid"},
                    {
                        "evidence_id": "CV-01",
                        "source": "jd",
                        "source_document_sha256": "bad",
                        "locator": "document",
                        "span_start": 0,
                        "span_end": 1,
                        "span_sha256": "bad",
                    },
                ],
            },
            {
                "claim_id": "DUP",
                "claim_type": "unknown",
                "text": "duplicate",
                "basis_claim_ids": [],
                "evidence_refs": [],
            },
            {
                "claim_id": "DUP",
                "claim_type": "unknown",
                "text": "duplicate again",
                "basis_claim_ids": [],
                "evidence_refs": [],
            },
            {
                "claim_id": "CLM-2",
                "claim_type": "knowledge",
                "text": "knowledge",
                "basis_claim_ids": [],
                "evidence_refs": [],
            },
            {
                "claim_id": "CLM-3",
                "claim_type": "derived_fact",
                "text": "computed",
                "basis_claim_ids": ["CLM-2"],
                "evidence_refs": [],
                "formula": {"operation": "sum"},
                "inputs": [1, 1],
                "value": 2,
            },
            {
                "claim_id": "CLM-4",
                "claim_type": "inference",
                "text": "inference",
                "basis_claim_ids": ["CLM-4"],
                "evidence_refs": [],
            },
        ]
        manifest = self.root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 9,
                    "report_sha256": "bad",
                    "input_sha256": {},
                    "claims": claims,
                }
            ),
            encoding="utf-8",
        )
        result, ambiguous = guard_report(report, manifest, self.request)
        codes = {item.code for item in result.findings}
        expected = {
            "PROVENANCE_SCHEMA_UNSUPPORTED",
            "REPORT_HASH_MISMATCH",
            "INPUT_HASH_SET_MISMATCH",
            "CLAIM_NOT_OBJECT",
            "CLAIM_ID_DUPLICATE",
            "HTML_REFERENCES_UNKNOWN_CLAIM",
            "UNBOUND_CLAIM_LABEL",
            "CLAIM_TEXT_NOT_BOUND",
            "CLAIM_LABEL_MISSING",
            "UNKNOWN_BASIS_CLAIM",
            "EVIDENCE_REF_INVALID",
            "EVIDENCE_ID_INVALID",
            "EVIDENCE_SOURCE_INVALID",
            "EVIDENCE_SOURCE_MISMATCH",
            "SOURCE_DOCUMENT_HASH_MISMATCH",
            "KNOWLEDGE_WITHOUT_SOURCE",
            "DERIVED_VALUE_NOT_RENDERED",
            "INFERENCE_WITHOUT_GROUNDED_BASIS",
        }
        self.assertTrue(expected <= codes, expected - codes)
        self.assertEqual(ambiguous, [])

    def _checker_script(self, body: str) -> Path:
        script = self.root / f"checker-{len(list(self.root.glob('checker-*')))}.py"
        script.write_text("import json, pathlib, sys\n" + body, encoding="utf-8")
        return script

    def test_semantic_checker_success_and_failures(self) -> None:
        self.assertEqual(run_semantic_checker("ignored", [], self.root), {})
        request = [{"claim_id": "CLM-1", "claim": "x", "evidence_spans": ["x"]}]
        success = self._checker_script(
            "pathlib.Path(sys.argv[2]).write_text(json.dumps({'schema_version': 1, 'claims': "
            "[{'claim_id': 'CLM-1', 'status': 'supported'}]}))\n"
        )
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(success))} {{request}} {{output}}"
        self.assertEqual(run_semantic_checker(command, request, self.root), {"CLM-1": "supported"})

        with self.assertRaisesRegex(ValueError, "Unknown semantic guard placeholder"):
            run_semantic_checker("tool {missing}", request, self.root)
        with self.assertRaisesRegex(ValueError, "empty"):
            run_semantic_checker("", request, self.root)

        failing = self._checker_script("raise SystemExit(2)\n")
        with self.assertRaisesRegex(RuntimeError, "exit code 2"):
            run_semantic_checker(f"{shlex.quote(sys.executable)} {shlex.quote(str(failing))}", request, self.root)

        no_output = self._checker_script("pass\n")
        with self.assertRaisesRegex(RuntimeError, "did not create"):
            run_semantic_checker(f"{shlex.quote(sys.executable)} {shlex.quote(str(no_output))}", request, self.root)

    def test_semantic_checker_rejects_invalid_output_shapes(self) -> None:
        request = [{"claim_id": "CLM-1", "claim": "x", "evidence_spans": ["x"]}]
        outputs = (
            ({"schema_version": 2, "claims": []}, "schema"),
            ({"schema_version": 1, "claims": ["bad"]}, "must be an object"),
            ({"schema_version": 1, "claims": [{"claim_id": 1, "status": "bad"}]}, "is invalid"),
        )
        for index, (payload, message) in enumerate(outputs):
            with self.subTest(index=index):
                script = self._checker_script(
                    f"pathlib.Path(sys.argv[1]).write_text({json.dumps(json.dumps(payload))})\n"
                )
                command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{output}}"
                with self.assertRaisesRegex(ValueError, message):
                    run_semantic_checker(command, request, self.root)


if __name__ == "__main__":
    unittest.main()
