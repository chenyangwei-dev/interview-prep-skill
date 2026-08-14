#!/usr/bin/env python3
"""Deterministic tests for DAG declarations and provenance guards."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from dag import CONTENT_DAG, RUNTIME_DAG, Dag, DagError, NodeSpec  # noqa: E402
from guards import guard_report  # noqa: E402
from provenance import sha256_path, sha256_text  # noqa: E402


class DagTests(unittest.TestCase):
    def test_every_declared_node_has_guards(self) -> None:
        for graph in (CONTENT_DAG, RUNTIME_DAG):
            self.assertTrue(graph.topological_order())
            self.assertTrue(all(graph.nodes[node_id].guards for node_id in graph.nodes))
        self.assertEqual(RUNTIME_DAG.topological_order()[-1], "finalize")

    def test_cycle_is_rejected(self) -> None:
        with self.assertRaises(DagError):
            Dag((NodeSpec("a", ("b",)), NodeSpec("b", ("a",))))


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.jd = self.root / "jd.md"
        self.resume = self.root / "resume.md"
        self.jd_text = "岗位要求后端系统设计能力。"
        self.jd.write_text(self.jd_text, encoding="utf-8")
        self.resume.write_text("参与支付项目。", encoding="utf-8")
        self.request = {
            "schema_version": 2,
            "inputs": {
                "jd": {
                    "normalized_path": str(self.jd),
                    "normalized_sha256": sha256_path(self.jd),
                },
                "resume": {
                    "normalized_path": str(self.resume),
                    "normalized_sha256": sha256_path(self.resume),
                },
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_artifacts(self, claim_text: str, *, label: str = "[JD事实｜JD-01]") -> tuple[Path, Path]:
        report = self.root / "report.html"
        manifest = self.root / "report.provenance.json"
        report.write_text(
            f'<!doctype html><html><body><p data-claim-id="CLM-001">{label} {claim_text}</p></body></html>',
            encoding="utf-8",
        )
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "report_sha256": sha256_path(report),
                    "input_sha256": {
                        name: metadata["normalized_sha256"]
                        for name, metadata in self.request["inputs"].items()
                    },
                    "claims": [
                        {
                            "claim_id": "CLM-001",
                            "claim_type": "source_fact",
                            "text": claim_text,
                            "basis_claim_ids": [],
                            "evidence_refs": [
                                {
                                    "evidence_id": "JD-01",
                                    "source": "jd",
                                    "source_document_sha256": sha256_path(self.jd),
                                    "locator": "document",
                                    "span_start": 0,
                                    "span_end": len(self.jd_text),
                                    "span_sha256": sha256_text(self.jd_text),
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return report, manifest

    def test_exact_source_fact_passes_without_model_checker(self) -> None:
        report, manifest = self._write_artifacts(self.jd_text)
        result, ambiguous = guard_report(report, manifest, self.request)
        self.assertTrue(result.passed, result.as_dict())
        self.assertEqual(ambiguous, [])

    def test_paraphrase_is_blocked_without_semantic_checker(self) -> None:
        report, manifest = self._write_artifacts("该岗位需要系统设计能力。")
        result, ambiguous = guard_report(report, manifest, self.request)
        self.assertFalse(result.passed)
        self.assertIn("SEMANTIC_AMBIGUOUS", {finding.code for finding in result.findings})
        self.assertEqual([item["claim_id"] for item in ambiguous], ["CLM-001"])

    def test_source_label_must_match_source(self) -> None:
        report, manifest = self._write_artifacts(self.jd_text, label="[简历事实｜JD-01]")
        result, _ = guard_report(report, manifest, self.request)
        self.assertFalse(result.passed)
        self.assertIn("CLAIM_SOURCE_LABEL_MISMATCH", {finding.code for finding in result.findings})

    def test_manifest_must_bind_the_full_input_hash_set(self) -> None:
        report, manifest = self._write_artifacts(self.jd_text)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["input_sha256"].pop("resume")
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result, _ = guard_report(report, manifest, self.request)
        self.assertFalse(result.passed)
        self.assertIn("INPUT_HASH_SET_MISMATCH", {finding.code for finding in result.findings})

    def test_inference_requires_semantic_support_from_grounded_basis(self) -> None:
        report, manifest = self._write_artifacts(self.jd_text)
        report.write_text(
            '<p data-claim-id="CLM-001">[JD事实｜JD-01] 岗位要求后端系统设计能力。</p>'
            '<p data-claim-id="CLM-002">[推断｜依据：JD-01] 面试可能包含系统设计题。</p>',
            encoding="utf-8",
        )
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["report_sha256"] = sha256_path(report)
        data["claims"].append(
            {
                "claim_id": "CLM-002",
                "claim_type": "inference",
                "text": "面试可能包含系统设计题。",
                "basis_claim_ids": ["CLM-001"],
                "evidence_refs": [],
            }
        )
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result, ambiguous = guard_report(report, manifest, self.request)
        self.assertFalse(result.passed)
        self.assertIn("CLM-002", [item["claim_id"] for item in ambiguous])
        supported, remaining = guard_report(report, manifest, self.request, {"CLM-002": "supported"})
        self.assertTrue(supported.passed, supported.as_dict())
        self.assertEqual(remaining, [])

    def test_derived_fact_is_recomputed(self) -> None:
        report, manifest = self._write_artifacts(self.jd_text)
        report.write_text(
            '<p data-claim-id="CLM-001">[JD事实｜JD-01] 岗位要求后端系统设计能力。</p>'
            '<p data-claim-id="CLM-002">[计算事实] 共 2 项。</p>',
            encoding="utf-8",
        )
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["report_sha256"] = sha256_path(report)
        data["claims"].append(
            {
                "claim_id": "CLM-002",
                "claim_type": "derived_fact",
                "text": "共 2 项。",
                "basis_claim_ids": ["CLM-001"],
                "evidence_refs": [],
                "formula": {"operation": "sum"},
                "inputs": [1, 1],
                "value": 3,
            }
        )
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result, _ = guard_report(report, manifest, self.request)
        self.assertFalse(result.passed)
        self.assertIn("DERIVED_FACT_NOT_REPLAYABLE", {finding.code for finding in result.findings})


class RunnerPublicationTests(unittest.TestCase):
    @staticmethod
    def _job(root: Path) -> Path:
        (root / "jd.md").write_text("岗位要求后端系统设计能力。", encoding="utf-8")
        (root / "resume.md").write_text("参与支付项目。", encoding="utf-8")
        job = root / "job.json"
        job.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "jd": {"path": "jd.md"},
                    "resume": {"path": "resume.md"},
                    "languages": {
                        "jd_language": "zh",
                        "resume_language": "zh",
                        "interview_language": "zh",
                        "report_language": "zh",
                        "answer_mode": "single",
                    },
                    "output": {"report_path": "out/final.html"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return job

    @staticmethod
    def _start(job: Path, run_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "run_interview_prep.py"),
                "start",
                "--job",
                str(job),
                "--run-dir",
                str(run_dir),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _manifest(request: dict[str, object], report: Path, claims: list[dict[str, object]]) -> dict[str, object]:
        inputs = request["inputs"]
        assert isinstance(inputs, dict)
        return {
            "schema_version": 1,
            "report_sha256": sha256_path(report),
            "input_sha256": {
                name: metadata["normalized_sha256"]
                for name, metadata in inputs.items()
                if isinstance(metadata, dict)
            },
            "claims": claims,
        }

    def test_invalid_html_never_reaches_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = self._job(root)
            run_dir = root / "run"
            final_report = root / "out" / "final.html"
            start = self._start(job, run_dir)
            self.assertEqual(start.returncode, 3, start.stderr)
            request = json.loads((run_dir / "generation-request.json").read_text(encoding="utf-8"))
            normalized_jd = Path(request["inputs"]["jd"]["normalized_path"])
            jd_text = normalized_jd.read_text(encoding="utf-8")
            candidate = root / "candidate.html"
            sidecar = root / "candidate.html.provenance.json"
            candidate.write_text(
                f'<p data-claim-id="CLM-001">[JD事实｜JD-01] {jd_text}</p>',
                encoding="utf-8",
            )
            sidecar.write_text(
                json.dumps(
                    self._manifest(
                        request,
                        candidate,
                        [
                            {
                                "claim_id": "CLM-001",
                                "claim_type": "source_fact",
                                "text": jd_text,
                                "basis_claim_ids": [],
                                "evidence_refs": [
                                    {
                                        "evidence_id": "JD-01",
                                        "source": "jd",
                                        "source_document_sha256": sha256_path(normalized_jd),
                                        "locator": "document",
                                        "span_start": 0,
                                        "span_end": len(jd_text),
                                        "span_sha256": sha256_text(jd_text),
                                    }
                                ],
                            }
                        ],
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            resume = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_interview_prep.py"),
                    "resume",
                    "--run-dir",
                    str(run_dir),
                    "--report",
                    str(candidate),
                    "--provenance",
                    str(sidecar),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(resume.returncode, 1)
            self.assertTrue((run_dir / "artifacts" / "report.html").is_file())
            self.assertFalse(final_report.exists())
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["nodes"]["validate_report"]["status"], "guard_failed")
            self.assertEqual(state["nodes"]["finalize"]["status"], "pending")

    def test_valid_report_is_published_only_by_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = self._job(root)
            run_dir = root / "run"
            start = self._start(job, run_dir)
            self.assertEqual(start.returncode, 3, start.stderr)
            request = json.loads((run_dir / "generation-request.json").read_text(encoding="utf-8"))
            jd = Path(request["inputs"]["jd"]["normalized_path"])
            resume_source = Path(request["inputs"]["resume"]["normalized_path"])
            jd_text = jd.read_text(encoding="utf-8")
            resume_text = resume_source.read_text(encoding="utf-8")
            candidate = root / "candidate.html"
            section_ids = (
                "overview",
                "match",
                "risks",
                "introductions",
                "stories",
                "questions",
                "deep-dive",
                "system-design",
                "management-interview",
                "reverse",
                "plan",
                "cheat-sheet",
                "evidence",
                "confirmations",
                "integrity",
            )
            sections = []
            for section_id in section_ids:
                content = f"<h2>{section_id}</h2>"
                if section_id == "overview":
                    content += (
                        f'<p data-claim-id="CLM-001">[JD事实｜JD-01] {jd_text}</p>'
                        f'<p data-claim-id="CLM-002">[简历事实｜CV-01] {resume_text}</p>'
                    )
                if section_id == "match":
                    content += '<div class="table-wrap"><table><tr><td>匹配</td></tr></table></div>'
                if section_id == "questions":
                    content += '<input id="question-search">'
                if section_id == "system-design":
                    content += "系统设计" + "".join(
                        '<details data-kind="system-design"><summary>题目</summary><p>方案</p></details>'
                        for _ in range(2)
                    )
                if section_id == "management-interview":
                    content += "管理层" + "".join(
                        '<details data-kind="management-interview"><summary>问题</summary><p>方案</p></details>'
                        for _ in range(6)
                    )
                sections.append(f'<section id="{section_id}">{content}</section>')
            candidate.write_text(
                "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Test</title>"
                "<style>@media print{nav{display:none}} @media (max-width:600px){main{width:100%}}</style>"
                "</head><body><header><div class=\"summary-grid\">摘要</div></header><nav>导航</nav><main>"
                '<button id="expand-all">展开</button><button id="collapse-all">收起</button>'
                '<button id="print-report">打印</button>'
                + "".join(sections)
                + "</main><script>document.querySelectorAll('button').forEach(function(button){"
                "button.addEventListener('click',function(){});});</script></body></html>",
                encoding="utf-8",
            )
            claims = []
            for claim_id, evidence_id, source_name, source_path, text_value in (
                ("CLM-001", "JD-01", "jd", jd, jd_text),
                ("CLM-002", "CV-01", "resume", resume_source, resume_text),
            ):
                claims.append(
                    {
                        "claim_id": claim_id,
                        "claim_type": "source_fact",
                        "text": text_value,
                        "basis_claim_ids": [],
                        "evidence_refs": [
                            {
                                "evidence_id": evidence_id,
                                "source": source_name,
                                "source_document_sha256": sha256_path(source_path),
                                "locator": "document",
                                "span_start": 0,
                                "span_end": len(text_value),
                                "span_sha256": sha256_text(text_value),
                            }
                        ],
                    }
                )
            sidecar = root / "candidate.html.provenance.json"
            sidecar.write_text(
                json.dumps(self._manifest(request, candidate, claims), ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertFalse((root / "out" / "final.html").exists())
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_interview_prep.py"),
                    "resume",
                    "--run-dir",
                    str(run_dir),
                    "--report",
                    str(candidate),
                    "--provenance",
                    str(sidecar),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            final_report = root / "out" / "final.html"
            final_sidecar = root / "out" / "final.html.provenance.json"
            self.assertEqual(final_report.read_bytes(), candidate.read_bytes())
            self.assertTrue(final_sidecar.is_file())
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["nodes"]["finalize"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
