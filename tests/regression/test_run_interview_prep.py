from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_interview_prep.py"
FIXTURE_GENERATOR = ROOT / "tests" / "regression" / "fixtures" / "generate_guarded_report.py"


class RunInterviewPrepTests(unittest.TestCase):
    @staticmethod
    def _minimal_job(root: Path) -> Path:
        (root / "jd.md").write_text("Synthetic JD", encoding="utf-8")
        (root / "resume.md").write_text("Synthetic resume", encoding="utf-8")
        job_path = root / "job.json"
        job_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "jd": {"path": "jd.md"},
                    "resume": {"path": "resume.md"},
                    "languages": {
                        "jd_language": "en",
                        "resume_language": "en",
                        "interview_language": "en",
                        "report_language": "en",
                        "answer_mode": "single",
                    },
                    "output": {"report_path": "output/report.html"},
                }
            ),
            encoding="utf-8",
        )
        return job_path

    @staticmethod
    def _fixture_command() -> str:
        return " ".join(
            [
                shlex.quote(sys.executable),
                shlex.quote(str(FIXTURE_GENERATOR)),
                "--request",
                "{request}",
                "--output",
                "{output}",
                "--provenance",
                "{provenance}",
            ]
        )

    @unittest.skipUnless(importlib.util.find_spec("langgraph") is not None, "LangGraph is unavailable")
    def test_langgraph_engine_runs_the_same_guarded_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            job_path = self._minimal_job(temp)
            run_dir = temp / "run"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "start",
                    "--job",
                    str(job_path),
                    "--run-dir",
                    str(run_dir),
                    "--generator-command",
                    self._fixture_command(),
                    "--engine",
                    "langgraph",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["orchestrator"], "langgraph")
            self.assertTrue((run_dir / "langgraph-checkpoints.sqlite").is_file())
            self.assertTrue((temp / "output" / "report.html").is_file())
            for node_id in ("prepare", "generate_report", "validate_report", "evaluate_report", "finalize"):
                self.assertIn(state["nodes"][node_id]["status"], {"completed", "skipped"})
                self.assertTrue((run_dir / "guards" / f"{node_id}.json").is_file())

    @unittest.skipUnless(importlib.util.find_spec("langgraph") is not None, "LangGraph is unavailable")
    def test_langgraph_waiting_run_resumes_with_the_recorded_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            job_path = self._minimal_job(temp)
            run_dir = temp / "run"
            started = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "start",
                    "--job",
                    str(job_path),
                    "--run-dir",
                    str(run_dir),
                    "--engine",
                    "langgraph",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(started.returncode, 3, started.stderr)
            first_checkpoint_size = (run_dir / "langgraph-checkpoints.sqlite").stat().st_size

            resumed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "resume",
                    "--run-dir",
                    str(run_dir),
                    "--generator-command",
                    self._fixture_command(),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["orchestrator"], "langgraph")
            self.assertGreaterEqual((run_dir / "langgraph-checkpoints.sqlite").stat().st_size, first_checkpoint_size)

    def test_generator_adapter_handles_placeholder_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            inputs = temp / "input files"
            inputs.mkdir()
            (inputs / "jd.md").write_text("Synthetic JD", encoding="utf-8")
            (inputs / "resume.md").write_text("Synthetic resume", encoding="utf-8")
            job = {
                "schema_version": 1,
                "jd": {"path": "input files/jd.md"},
                "resume": {"path": "input files/resume.md"},
                "languages": {
                    "jd_language": "en",
                    "resume_language": "en",
                    "interview_language": "en",
                    "report_language": "en",
                    "answer_mode": "single",
                },
                "output": {"report_path": "output files/report.html"},
            }
            job_path = temp / "job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            command_template = " ".join(
                [
                    shlex.quote(sys.executable),
                    shlex.quote(str(FIXTURE_GENERATOR)),
                    "--request",
                    "{request}",
                    "--output",
                    "{output}",
                    "--provenance",
                    "{provenance}",
                ]
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "start",
                    "--job",
                    str(job_path),
                    "--run-dir",
                    str(temp / "run with spaces"),
                    "--generator-command",
                    command_template,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            state = json.loads((temp / "run with spaces" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["completed_steps"], ["prepare", "generate", "validate", "evaluate"])

    def test_prepare_wait_resume_and_logs_do_not_contain_input_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            inputs = temp / "inputs"
            inputs.mkdir()
            private_jd = "Synthetic confidential JD phrase 7f3c"
            private_resume = "Synthetic private resume phrase 9a2d"
            (inputs / "jd.md").write_text(private_jd, encoding="utf-8")
            (inputs / "resume.md").write_text(private_resume, encoding="utf-8")
            eval_case = {
                "schema_version": 1,
                "case_id": "runner-synthetic",
                "expected": {
                    "allowed_evidence_ids": ["JD-01", "CV-01"],
                    "required_phrases": ["合成测试内容"],
                    "max_unresolved_confirmations": 999,
                },
                "private_test_values": [private_resume],
            }
            (temp / "eval-case.json").write_text(json.dumps(eval_case), encoding="utf-8")
            job = {
                "schema_version": 1,
                "jd": {"path": "inputs/jd.md"},
                "resume": {"path": "inputs/resume.md"},
                "languages": {
                    "jd_language": "zh",
                    "resume_language": "zh",
                    "interview_language": "zh",
                    "report_language": "zh",
                    "answer_mode": "single",
                },
                "output": {"report_path": "outputs/report.html"},
                "evaluation": {"case_path": "eval-case.json"},
            }
            job_path = temp / "job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            run_dir = temp / "run"

            started = subprocess.run(
                [sys.executable, str(RUNNER), "start", "--job", str(job_path), "--run-dir", str(run_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(started.returncode, 3, started.stderr)
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "waiting_for_generation")
            request = json.loads((run_dir / "generation-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["languages"]["interview_language"], "zh")

            events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(private_jd, events)
            self.assertNotIn(private_resume, events)

            report = temp / "generated.html"
            provenance = temp / "generated.html.provenance.json"
            generated = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_GENERATOR),
                    "--request",
                    str(run_dir / "generation-request.json"),
                    "--output",
                    str(report),
                    "--provenance",
                    str(provenance),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            resumed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "resume",
                    "--run-dir",
                    str(run_dir),
                    "--report",
                    str(report),
                    "--provenance",
                    str(provenance),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            final_state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(final_state["status"], "completed")
            self.assertEqual(final_state["completed_steps"], ["prepare", "generate", "validate", "evaluate"])
            self.assertTrue((temp / "outputs" / "report.html").is_file())
            evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
            self.assertTrue(evaluation["passed"])

    def test_job_validation_rejects_invalid_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            job_path = temp / "job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "jd": {"path": "jd.md"},
                        "resume": {"path": "resume.md"},
                        "languages": {
                            "jd_language": "unknown",
                            "resume_language": "zh",
                            "interview_language": "zh",
                            "report_language": "zh",
                            "answer_mode": "single",
                        },
                        "output": {"report_path": "report.html"},
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(RUNNER), "start", "--job", str(job_path), "--run-dir", str(temp / "run")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("invalid languages.jd_language", completed.stderr)


if __name__ == "__main__":
    unittest.main()
