#!/usr/bin/env python3
"""Run the interview-prep workflow with checkpoints and privacy-safe events."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval_report import evaluate_path, load_case


ROOT = Path(__file__).resolve().parents[1]
WAITING_FOR_GENERATION = 3
LANGUAGE_FIELDS = {
    "jd_language": {"zh", "en", "mixed"},
    "resume_language": {"zh", "en", "mixed"},
    "interview_language": {"zh", "en", "bilingual"},
    "report_language": {"zh", "en"},
    "answer_mode": {"single", "bilingual"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class RunContext:
    def __init__(self, run_dir: Path, state: dict[str, Any]) -> None:
        self.run_dir = run_dir
        self.state_path = run_dir / "state.json"
        self.events_path = run_dir / "events.jsonl"
        self.state = state

    def event(self, step: str, status: str, **metadata: Any) -> None:
        record = {"timestamp": utc_now(), "step": step, "status": status, **metadata}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def checkpoint(self, status: str, **updates: Any) -> None:
        self.state.update(updates)
        self.state["status"] = status
        self.state["updated_at"] = utc_now()
        write_json(self.state_path, self.state)


def load_job(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("job.schema_version must be 1")
    for key in ("jd", "resume", "languages", "output"):
        if key not in data:
            raise ValueError(f"job is missing required field: {key}")
    for key, allowed in LANGUAGE_FIELDS.items():
        value = data["languages"].get(key)
        if value not in allowed:
            raise ValueError(f"invalid languages.{key}: {value}")
    evaluation = data.get("evaluation")
    if evaluation is not None and (not isinstance(evaluation, dict) or not evaluation.get("case_path")):
        raise ValueError("job.evaluation.case_path is required when evaluation is configured")
    return data


def resolve_job_path(job_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = job_path.parent / path
    return path.resolve()


def normalize_input(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"Input does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        command = [sys.executable, str(ROOT / "scripts" / "extract_pdf.py"), str(source), "--output", str(destination)]
        method = "extract_pdf"
    elif suffix == ".docx":
        command = [sys.executable, str(ROOT / "scripts" / "extract_docx.py"), str(source), "--output", str(destination)]
        method = "extract_docx"
    elif suffix in {".md", ".txt"}:
        shutil.copyfile(source, destination)
        command = None
        method = "copy_text"
    else:
        raise ValueError(f"Unsupported input type: {source.suffix}")

    if command:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"{method} failed: {detail}")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError(f"Normalized input is empty: {destination}")
    return {
        "source_name": source.name,
        "source_sha256": sha256(source),
        "source_bytes": source.stat().st_size,
        "normalized_path": str(destination),
        "normalized_sha256": sha256(destination),
        "normalization_method": method,
    }


def prepare_request(context: RunContext, job_path: Path, job: dict[str, Any]) -> Path:
    started = time.monotonic()
    context.event("prepare", "started")
    inputs_dir = context.run_dir / "inputs"
    jd_source = resolve_job_path(job_path, job["jd"]["path"])
    resume_source = resolve_job_path(job_path, job["resume"]["path"])
    jd_meta = normalize_input(jd_source, inputs_dir / "jd.normalized.md")
    resume_meta = normalize_input(resume_source, inputs_dir / "resume.normalized.md")

    requested_output = resolve_job_path(job_path, job["output"]["report_path"])
    requested_output.parent.mkdir(parents=True, exist_ok=True)
    eval_case_path = None
    if job.get("evaluation"):
        eval_case_path = resolve_job_path(job_path, job["evaluation"]["case_path"])
        load_case(eval_case_path)
    request = {
        "schema_version": 1,
        "run_id": context.state["run_id"],
        "skill_root": str(ROOT),
        "skill_version": context.state["skill_version"],
        "inputs": {"jd": jd_meta, "resume": resume_meta},
        "languages": job["languages"],
        "target": {"report_path": str(requested_output)},
        "instructions": [
            "Use SKILL.md and its required references as the authoritative workflow.",
            "Read the normalized JD and resume files; do not infer facts from file names.",
            "Generate the self-contained report at target.report_path.",
            "Do not copy private contact details into the report or run logs.",
            "Run the resume command after generation to validate and finalize the run.",
        ],
    }
    request_path = context.run_dir / "generation-request.json"
    write_json(request_path, request)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    context.event(
        "prepare",
        "completed",
        duration_ms=elapsed_ms,
        jd_bytes=jd_meta["source_bytes"],
        resume_bytes=resume_meta["source_bytes"],
    )
    state_updates: dict[str, Any] = {
        "request_path": str(request_path),
        "report_path": str(requested_output),
        "completed_steps": ["prepare"],
    }
    if eval_case_path:
        state_updates["eval_case_path"] = str(eval_case_path)
    context.checkpoint("prepared", **state_updates)
    return request_path


def run_generator(context: RunContext, command_template: str) -> None:
    request_path = Path(context.state["request_path"])
    report_path = Path(context.state["report_path"])
    replacements = {"request": str(request_path), "output": str(report_path), "run_dir": str(context.run_dir)}
    try:
        command = [argument.format(**replacements) for argument in shlex.split(command_template)]
    except KeyError as exc:
        raise ValueError(f"Unknown generator placeholder: {exc.args[0]}") from exc
    if not command:
        raise ValueError("Generator command is empty")

    started = time.monotonic()
    context.event("generate", "started", executable=Path(command[0]).name)
    completed = subprocess.run(command, cwd=context.run_dir, capture_output=True, text=True, check=False)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if completed.returncode:
        context.event("generate", "failed", duration_ms=elapsed_ms, returncode=completed.returncode)
        raise RuntimeError(f"Generator failed with exit code {completed.returncode}")
    context.event("generate", "completed", duration_ms=elapsed_ms, returncode=0)
    completed_steps = list(context.state.get("completed_steps", []))
    if "generate" not in completed_steps:
        completed_steps.append("generate")
    context.checkpoint("generated", completed_steps=completed_steps)


def adopt_report(context: RunContext, report: Path) -> None:
    target = Path(context.state["report_path"])
    report = report.expanduser().resolve()
    if not report.is_file():
        raise FileNotFoundError(f"Report does not exist: {report}")
    if report != target:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report, target)
    completed_steps = list(context.state.get("completed_steps", []))
    if "generate" not in completed_steps:
        completed_steps.append("generate")
    context.event("generate", "adopted", report_bytes=target.stat().st_size, report_sha256=sha256(target))
    context.checkpoint("generated", completed_steps=completed_steps)


def validate_and_finalize(context: RunContext) -> int:
    report = Path(context.state["report_path"])
    if not report.is_file():
        context.checkpoint("waiting_for_generation")
        context.event("generate", "waiting", request_path=context.state["request_path"])
        print(f"WAITING_FOR_GENERATION: {context.state['request_path']}")
        return WAITING_FOR_GENERATION

    started = time.monotonic()
    context.event("validate", "started")
    command = [sys.executable, str(ROOT / "scripts" / "validate_report.py"), str(report)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    validation = {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    write_json(context.run_dir / "validation.json", validation)
    if completed.returncode:
        context.event("validate", "failed", duration_ms=elapsed_ms, returncode=completed.returncode)
        context.checkpoint("validation_failed", validation_path=str(context.run_dir / "validation.json"))
        print(completed.stderr, file=sys.stderr, end="")
        return 1

    completed_steps = list(context.state.get("completed_steps", []))
    if "validate" not in completed_steps:
        completed_steps.append("validate")
    context.event(
        "validate",
        "completed",
        duration_ms=elapsed_ms,
        report_bytes=report.stat().st_size,
        report_sha256=sha256(report),
    )
    checkpoint_updates: dict[str, Any] = {
        "completed_steps": completed_steps,
        "validation_path": str(context.run_dir / "validation.json"),
        "report_sha256": sha256(report),
    }
    eval_case_path = context.state.get("eval_case_path")
    if eval_case_path:
        eval_started = time.monotonic()
        context.event("evaluate", "started")
        evaluation = evaluate_path(report, load_case(Path(eval_case_path)))
        evaluation_path = context.run_dir / "evaluation.json"
        write_json(evaluation_path, evaluation)
        eval_elapsed_ms = round((time.monotonic() - eval_started) * 1000)
        if not evaluation["passed"]:
            context.event(
                "evaluate", "failed", duration_ms=eval_elapsed_ms, error_count=evaluation["error_count"]
            )
            context.checkpoint(
                "evaluation_failed",
                **checkpoint_updates,
                evaluation_path=str(evaluation_path),
            )
            print(f"EVALUATION_FAILED: {evaluation_path}", file=sys.stderr)
            return 1
        context.event("evaluate", "completed", duration_ms=eval_elapsed_ms, error_count=0)
        completed_steps.append("evaluate")
        checkpoint_updates["evaluation_path"] = str(evaluation_path)

    context.checkpoint("completed", **checkpoint_updates)
    print(f"COMPLETED: {report}")
    return 0


def git_version() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def start(args: argparse.Namespace) -> int:
    job_path = args.job.expanduser().resolve()
    try:
        job = load_job(job_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = (args.run_dir or ROOT / "work" / "runs" / run_id).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    state = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "created",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "skill_version": git_version(),
        "job_path": str(job_path),
        "completed_steps": [],
    }
    context = RunContext(run_dir, state)
    context.checkpoint("created")
    try:
        prepare_request(context, job_path, job)
        if args.generator_command:
            run_generator(context, args.generator_command)
        return validate_and_finalize(context)
    except Exception as exc:
        context.event("pipeline", "failed", error_type=type(exc).__name__)
        context.checkpoint("failed", error_type=type(exc).__name__, error_message=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def resume(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        print(f"ERROR: Missing state file: {state_path}", file=sys.stderr)
        return 2
    context = RunContext(run_dir, json.loads(state_path.read_text(encoding="utf-8")))
    try:
        if args.report:
            adopt_report(context, args.report)
        elif args.generator_command and not Path(context.state["report_path"]).is_file():
            run_generator(context, args.generator_command)
        return validate_and_finalize(context)
    except Exception as exc:
        context.event("pipeline", "failed", error_type=type(exc).__name__)
        context.checkpoint("failed", error_type=type(exc).__name__, error_message=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Create a run, normalize inputs, and prepare generation")
    start_parser.add_argument("--job", type=Path, required=True)
    start_parser.add_argument("--run-dir", type=Path)
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--generator-command")
    start_parser.set_defaults(handler=start)

    resume_parser = subparsers.add_parser("resume", help="Continue a prepared run and validate its report")
    resume_parser.add_argument("--run-dir", type=Path, required=True)
    resume_parser.add_argument("--report", type=Path)
    resume_parser.add_argument("--generator-command")
    resume_parser.set_defaults(handler=resume)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
