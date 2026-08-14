from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_interview_prep as runner  # noqa: E402


class RunnerHelperTests(unittest.TestCase):
    @staticmethod
    def _valid_job() -> dict[str, object]:
        return {
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
            "output": {"report_path": "report.html"},
        }

    def _load(self, root: Path, data: dict[str, object]) -> dict[str, object]:
        path = root / "job.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return runner.load_job(path)

    def test_load_job_accepts_extended_valid_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self._valid_job()
            job.update(
                {
                    "user": {"path": "user.md"},
                    "sources": [{"id": "SRC-01", "path": "source.md"}],
                    "evaluation": {"case_path": "case.json"},
                    "guards": {"semantic_command": "checker {request} {output}"},
                }
            )
            self.assertEqual(self._load(Path(directory), job)["schema_version"], 1)

    def test_load_job_rejects_schema_and_required_shapes(self) -> None:
        mutations = (
            (lambda job: job.update(schema_version=2), "schema_version"),
            (lambda job: job.pop("jd"), "missing required"),
            (lambda job: job.update(jd={}), "job.jd.path"),
            (lambda job: job.update(resume="bad"), "job.resume.path"),
            (lambda job: job.update(output={}), "output.report_path"),
            (lambda job: job.update(user={}), "user.path"),
            (lambda job: job.update(sources={}), "sources must be a list"),
            (lambda job: job.update(sources=[{}]), "requires path"),
            (lambda job: job.update(sources=[{"id": "BAD", "path": "x"}]), "SRC-nn"),
            (
                lambda job: job.update(
                    sources=[{"id": "SRC-01", "path": "x"}, {"id": "SRC-01", "path": "y"}]
                ),
                "duplicate",
            ),
            (lambda job: job["languages"].update(jd_language="bad"), "invalid languages"),  # type: ignore[union-attr]
            (lambda job: job.update(evaluation={}), "evaluation.case_path"),
            (lambda job: job.update(guards="bad"), "guards must be an object"),
            (lambda job: job.update(guards={"semantic_command": 1}), "semantic_command"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mutate, message in mutations:
                job = self._valid_job()
                mutate(job)
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    self._load(root, job)

    def test_normalize_input_text_and_error_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.md"
            source.write_text("content", encoding="utf-8")
            destination = root / "normalized.md"
            metadata = runner.normalize_input(source, destination)
            self.assertEqual(metadata["normalization_method"], "copy_text")
            self.assertEqual(destination.read_text(encoding="utf-8"), "content")

            with self.assertRaises(FileNotFoundError):
                runner.normalize_input(root / "missing.md", destination)
            unsupported = root / "input.csv"
            unsupported.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                runner.normalize_input(unsupported, destination)

            pdf = root / "input.pdf"
            pdf.write_bytes(b"pdf")
            failed = subprocess.CompletedProcess([], 2, stdout="", stderr="extract failed")
            with mock.patch.object(runner.subprocess, "run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "extract_pdf failed"):
                    runner.normalize_input(pdf, root / "pdf.md")

            docx = root / "input.docx"
            docx.write_bytes(b"docx")

            def successful_extract(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_text("extracted", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch.object(runner.subprocess, "run", side_effect=successful_extract):
                extracted = runner.normalize_input(docx, root / "docx.md")
            self.assertEqual(extracted["normalization_method"], "extract_docx")

            with mock.patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ):
                with self.assertRaisesRegex(RuntimeError, "empty"):
                    runner.normalize_input(docx, root / "empty.md")

    def test_skill_version_and_state_migration(self) -> None:
        with mock.patch.object(
            runner.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        ):
            self.assertTrue(runner.skill_version().startswith("sha256:"))

        current = {"schema_version": 2}
        self.assertIs(runner.migrate_state(current), current)
        self.assertEqual(current["nodes"], {})
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            runner.migrate_state({"schema_version": 9})
        migrated = runner.migrate_state({"schema_version": 1, "completed_steps": ["prepare", "validate"]})
        self.assertEqual(migrated["nodes"]["prepare"]["status"], "completed")
        self.assertEqual(migrated["nodes"]["generate_report"]["status"], "pending")
        self.assertEqual(migrated["migrated_from_schema"], 1)

    def test_run_context_tracks_success_failure_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = runner.RunContext(
                root,
                {"schema_version": 2, "run_id": "run", "nodes": {}, "completed_steps": []},
            )
            self.assertEqual(context.node_status("prepare"), "pending")
            context.begin_node("prepare")
            self.assertEqual(context.state["nodes"]["prepare"]["attempt"], 1)
            guard = root / "guard.json"
            output = root / "output.json"
            guard.write_text("{}", encoding="utf-8")
            output.write_text("{}", encoding="utf-8")
            context.finish_node("prepare", guard_path=guard, output_path=output, item_count=1)
            context.finish_node("prepare", guard_path=guard, output_path=output)
            self.assertEqual(context.state["completed_steps"], ["prepare"])
            self.assertIn("guard_sha256", context.state["nodes"]["prepare"])
            self.assertIn("output_sha256", context.state["nodes"]["prepare"])
            context.fail_node("generate_report", "guard_failed", "GuardFailure", finding_codes=["BAD"])
            self.assertEqual(context.state["status"], "guard_failed")
            self.assertIn("GuardFailure", context.events_path.read_text(encoding="utf-8"))

    def test_stage_generator_plan_status_and_resume_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("x", encoding="utf-8")
            destination = root / "destination.txt"
            self.assertEqual(runner._stage_file(source, destination), destination.resolve())
            self.assertEqual(destination.read_text(encoding="utf-8"), "x")
            self.assertEqual(runner._stage_file(destination, destination), destination.resolve())
            with self.assertRaises(FileNotFoundError):
                runner._stage_file(root / "missing", destination)

            context = runner.RunContext(
                root / "run",
                {
                    "schema_version": 2,
                    "run_id": "run",
                    "nodes": {"generate_report": {"status": "pending"}},
                    "request_path": str(source),
                    "staging_report_path": str(root / "run" / "report.html"),
                    "staging_provenance_path": str(root / "run" / "provenance.json"),
                },
            )
            with self.assertRaisesRegex(ValueError, "Unknown generator placeholder"):
                runner.run_generator(context, "tool {missing}")
            with self.assertRaisesRegex(ValueError, "empty"):
                runner.run_generator(context, "")
            with mock.patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 5, stdout="", stderr=""),
            ):
                with self.assertRaisesRegex(RuntimeError, "exit code 5"):
                    runner.run_generator(context, f"{sys.executable} -c pass")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(runner.wait_for_generation(context), runner.WAITING_FOR_GENERATION)
                self.assertEqual(runner.show_plan(argparse.Namespace(runtime=True, json=True)), 0)
            self.assertIn("WAITING_FOR_GENERATION", stdout.getvalue())
            self.assertIn('"nodes"', stdout.getvalue())

            missing_args = argparse.Namespace(run_dir=root / "missing")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(runner.show_status(missing_args), 2)
                self.assertEqual(runner.resume(argparse.Namespace(run_dir=root / "missing")), 2)
            self.assertIn("Missing state file", stderr.getvalue())

            context.checkpoint("waiting")
            status_output = io.StringIO()
            with contextlib.redirect_stdout(status_output):
                self.assertEqual(runner.show_status(argparse.Namespace(run_dir=context.run_dir)), 0)
            safe_status = json.loads(status_output.getvalue())
            self.assertEqual(safe_status["run_id"], "run")
            self.assertNotIn("request_path", safe_status)


if __name__ == "__main__":
    unittest.main()
