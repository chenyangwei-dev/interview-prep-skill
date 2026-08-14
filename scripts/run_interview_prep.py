#!/usr/bin/env python3
"""Run interview-prep with DAG state, staged artifacts, and blocking guards."""

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

from dag import CONTENT_DAG, RUNTIME_DAG
from eval_report import evaluate_path, load_case
from guards import (
    GuardFailure,
    guard_prepare_request,
    guard_report,
    result_for_command,
    run_semantic_checker,
    write_guard_result,
)


ROOT = Path(__file__).resolve().parents[1]
WAITING_FOR_GENERATION = 3
LANGUAGE_FIELDS = {
    "jd_language": {"zh", "en", "mixed"},
    "resume_language": {"zh", "en", "mixed"},
    "interview_language": {"zh", "en", "bilingual"},
    "report_language": {"zh", "en"},
    "answer_mode": {"single", "bilingual"},
}
SUCCESS_STATUSES = {"completed", "completed_empty", "completed_with_warning", "skipped"}


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


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def load_job(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("job.schema_version must be 1")
    for key in ("jd", "resume", "languages", "output"):
        if key not in data:
            raise ValueError(f"job is missing required field: {key}")
    for key in ("jd", "resume"):
        if not isinstance(data[key], dict) or not isinstance(data[key].get("path"), str):
            raise ValueError(f"job.{key}.path is required")
    if not isinstance(data["output"], dict) or not isinstance(data["output"].get("report_path"), str):
        raise ValueError("job.output.report_path is required")
    user_input = data.get("user")
    if user_input is not None and (not isinstance(user_input, dict) or not isinstance(user_input.get("path"), str)):
        raise ValueError("job.user.path is required when user input is configured")
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("job.sources must be a list")
    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError("each job.sources item requires path")
        source_id = source.get("id")
        if (
            not isinstance(source_id, str)
            or not source_id.startswith("SRC-")
            or len(source_id[4:]) < 2
            or not source_id[4:].isdigit()
        ):
            raise ValueError("each job.sources item requires an SRC-nn id")
        if source_id in source_ids:
            raise ValueError(f"duplicate job.sources id: {source_id}")
        source_ids.add(source_id)
    for key, allowed in LANGUAGE_FIELDS.items():
        value = data["languages"].get(key)
        if value not in allowed:
            raise ValueError(f"invalid languages.{key}: {value}")
    evaluation = data.get("evaluation")
    if evaluation is not None and (not isinstance(evaluation, dict) or not evaluation.get("case_path")):
        raise ValueError("job.evaluation.case_path is required when evaluation is configured")
    guards = data.get("guards")
    if guards is not None:
        if not isinstance(guards, dict):
            raise ValueError("job.guards must be an object")
        command = guards.get("semantic_command")
        if command is not None and not isinstance(command, str):
            raise ValueError("job.guards.semantic_command must be a string")
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


def skill_version() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return completed.stdout.strip()
    digest = hashlib.sha256()
    candidates = [ROOT / "SKILL.md"]
    candidates.extend(sorted((ROOT / "scripts").glob("*.py")))
    candidates.extend(sorted((ROOT / "references").glob("*.md")))
    for path in candidates:
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") == 2:
        state.setdefault("nodes", {})
        return state
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported state schema")
    completed = set(state.get("completed_steps", []))
    mapping = {
        "prepare": "prepare",
        "generate": "generate_report",
        "validate": "validate_report",
        "evaluate": "evaluate_report",
    }
    nodes = {
        node_id: {"status": "completed" if legacy in completed else "pending"}
        for legacy, node_id in mapping.items()
    }
    state["schema_version"] = 2
    state["nodes"] = nodes
    state["nodes"].setdefault("finalize", {"status": "pending"})
    state["migrated_from_schema"] = 1
    return state


class RunContext:
    def __init__(self, run_dir: Path, state: dict[str, Any]) -> None:
        self.run_dir = run_dir
        self.state_path = run_dir / "state.json"
        self.events_path = run_dir / "events.jsonl"
        self.state = migrate_state(state)

    def event(self, step: str, status: str, **metadata: Any) -> None:
        record = {"timestamp": utc_now(), "step": step, "status": status, **metadata}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def checkpoint(self, status: str | None = None, **updates: Any) -> None:
        self.state.update(updates)
        if status is not None:
            self.state["status"] = status
        self.state["updated_at"] = utc_now()
        write_json(self.state_path, self.state)

    def node_status(self, node_id: str) -> str:
        return self.state.setdefault("nodes", {}).get(node_id, {}).get("status", "pending")

    def begin_node(self, node_id: str) -> None:
        entry = self.state.setdefault("nodes", {}).setdefault(node_id, {})
        entry["status"] = "running"
        entry["attempt"] = int(entry.get("attempt", 0)) + 1
        entry["started_at"] = utc_now()
        self.event(node_id, "started", attempt=entry["attempt"])
        self.checkpoint()

    def finish_node(
        self,
        node_id: str,
        *,
        status: str = "completed",
        guard_path: Path | None = None,
        output_path: Path | None = None,
        **metadata: Any,
    ) -> None:
        entry = self.state.setdefault("nodes", {}).setdefault(node_id, {})
        entry.update(metadata)
        entry["status"] = status
        entry["completed_at"] = utc_now()
        if guard_path:
            entry["guard_path"] = str(guard_path)
            entry["guard_sha256"] = sha256(guard_path)
        if output_path and output_path.is_file():
            entry["output_path"] = str(output_path)
            entry["output_sha256"] = sha256(output_path)
        legacy_name = {
            "prepare": "prepare",
            "generate_report": "generate",
            "validate_report": "validate",
            "evaluate_report": "evaluate",
        }.get(node_id)
        completed_steps = list(self.state.get("completed_steps", []))
        if legacy_name and status in SUCCESS_STATUSES and legacy_name not in completed_steps:
            completed_steps.append(legacy_name)
        self.state["completed_steps"] = completed_steps
        self.event(node_id, status, **metadata)
        self.checkpoint()

    def fail_node(self, node_id: str, status: str, error_type: str, **metadata: Any) -> None:
        entry = self.state.setdefault("nodes", {}).setdefault(node_id, {})
        entry.update(metadata)
        entry["status"] = status
        entry["error_type"] = error_type
        entry["completed_at"] = utc_now()
        self.event(node_id, status, error_type=error_type, **metadata)
        self.checkpoint(status)


def prepare_request(
    context: RunContext,
    job_path: Path,
    job: dict[str, Any],
    semantic_guard_override: str | None = None,
) -> Path:
    context.begin_node("prepare")
    started = time.monotonic()
    inputs_dir = context.run_dir / "inputs"
    jd_source = resolve_job_path(job_path, job["jd"]["path"])
    resume_source = resolve_job_path(job_path, job["resume"]["path"])
    inputs = {
        "jd": normalize_input(jd_source, inputs_dir / "jd.normalized.md"),
        "resume": normalize_input(resume_source, inputs_dir / "resume.normalized.md"),
    }
    if job.get("user"):
        user_source = resolve_job_path(job_path, job["user"]["path"])
        inputs["user"] = normalize_input(user_source, inputs_dir / "user.normalized.md")
    for source in job.get("sources", []):
        source_id = source["id"]
        source_path = resolve_job_path(job_path, source["path"])
        metadata = normalize_input(source_path, inputs_dir / f"{source_id.lower()}.normalized.md")
        metadata["evidence_id"] = source_id
        if isinstance(source.get("url"), str):
            metadata["url"] = source["url"]
        if isinstance(source.get("accessed_at"), str):
            metadata["accessed_at"] = source["accessed_at"]
        inputs[f"src:{source_id}"] = metadata

    requested_output = resolve_job_path(job_path, job["output"]["report_path"])
    staging_report = context.run_dir / "staging" / "report.html"
    staging_provenance = context.run_dir / "staging" / "report.provenance.json"
    promoted_report = context.run_dir / "artifacts" / "report.html"
    promoted_provenance = context.run_dir / "artifacts" / "report.provenance.json"
    final_provenance = Path(str(requested_output) + ".provenance.json")
    eval_case_path = None
    if job.get("evaluation"):
        eval_case_path = resolve_job_path(job_path, job["evaluation"]["case_path"])
        load_case(eval_case_path)
    semantic_command = semantic_guard_override
    if semantic_command is None:
        semantic_command = (job.get("guards") or {}).get("semantic_command")

    request = {
        "schema_version": 2,
        "run_id": context.state["run_id"],
        "skill_root": str(ROOT),
        "skill_version": context.state["skill_version"],
        "inputs": inputs,
        "languages": job["languages"],
        "target": {
            "report_path": str(staging_report),
            "provenance_path": str(staging_provenance),
            "final_report_path": str(requested_output),
            "final_provenance_path": str(final_provenance),
        },
        "instructions": [
            "Use SKILL.md and its required references as the authoritative workflow.",
            "Read normalized inputs; do not infer facts from file names.",
            "Write the self-contained HTML report at target.report_path.",
            "Write a schema-version-1 claim provenance manifest at target.provenance_path.",
            "Copy every inputs.*.normalized_sha256 into manifest.input_sha256 using the same input key.",
            "Wrap every rendered claim in an element with data-claim-id matching the manifest.",
            "Source facts require exact source locators, spans, document hashes, and span hashes.",
            "Inferences require grounded basis_claim_ids and semantic support; derived facts require a replayable formula object.",
            "Label inferences, recommendations, assumptions, knowledge references, and unknowns explicitly.",
            "Do not copy private contact details into the report, state, or event logs.",
        ],
    }
    request_path = context.run_dir / "generation-request.json"
    write_json(request_path, request)
    guard_path = context.run_dir / "guards" / "prepare.json"
    result = guard_prepare_request(request)
    write_guard_result(guard_path, result)
    if not result.passed:
        context.fail_node("prepare", "guard_failed", "GuardFailure", guard_path=str(guard_path))
        raise GuardFailure(result)

    elapsed_ms = round((time.monotonic() - started) * 1000)
    state_updates: dict[str, Any] = {
        "request_path": str(request_path),
        "report_path": str(promoted_report),
        "final_report_path": str(requested_output),
        "staging_report_path": str(staging_report),
        "staging_provenance_path": str(staging_provenance),
        "provenance_path": str(promoted_provenance),
        "final_provenance_path": str(final_provenance),
        "semantic_guard_command": semantic_command,
    }
    if eval_case_path:
        state_updates["eval_case_path"] = str(eval_case_path)
    context.checkpoint("prepared", **state_updates)
    context.finish_node(
        "prepare",
        guard_path=guard_path,
        output_path=request_path,
        duration_ms=elapsed_ms,
        jd_bytes=inputs["jd"]["source_bytes"],
        resume_bytes=inputs["resume"]["source_bytes"],
        input_count=len(inputs),
    )
    return request_path


def _stage_file(source: Path, destination: Path) -> Path:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Artifact does not exist: {source}")
    if source != destination:
        atomic_copy(source, destination)
    return destination


def guard_and_promote_report(context: RunContext, report: Path, provenance: Path) -> None:
    context.begin_node("generate_report")
    started = time.monotonic()
    staged_report = _stage_file(report, Path(context.state["staging_report_path"]))
    staged_provenance = _stage_file(provenance, Path(context.state["staging_provenance_path"]))
    request = json.loads(Path(context.state["request_path"]).read_text(encoding="utf-8"))

    result, ambiguous = guard_report(staged_report, staged_provenance, request)
    semantic_command = context.state.get("semantic_guard_command")
    if ambiguous and isinstance(semantic_command, str) and semantic_command.strip():
        semantic_results = run_semantic_checker(semantic_command, ambiguous, context.run_dir)
        result, _ = guard_report(staged_report, staged_provenance, request, semantic_results)

    guard_path = context.run_dir / "guards" / "generate_report.json"
    write_guard_result(guard_path, result)
    if not result.passed:
        codes = sorted({finding.code for finding in result.findings})
        context.fail_node(
            "generate_report",
            "guard_failed",
            "GuardFailure",
            guard_path=str(guard_path),
            finding_codes=codes,
        )
        raise GuardFailure(result)

    promoted_report = Path(context.state["report_path"])
    promoted_provenance = Path(context.state["provenance_path"])
    atomic_copy(staged_report, promoted_report)
    atomic_copy(staged_provenance, promoted_provenance)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    context.finish_node(
        "generate_report",
        guard_path=guard_path,
        output_path=promoted_report,
        duration_ms=elapsed_ms,
        provenance_path=str(promoted_provenance),
        provenance_sha256=sha256(promoted_provenance),
    )
    context.checkpoint("generated")


def run_generator(context: RunContext, command_template: str) -> None:
    request_path = Path(context.state["request_path"])
    report_path = Path(context.state["staging_report_path"])
    provenance_path = Path(context.state["staging_provenance_path"])
    replacements = {
        "request": str(request_path),
        "output": str(report_path),
        "provenance": str(provenance_path),
        "run_dir": str(context.run_dir),
    }
    try:
        command = [argument.format(**replacements) for argument in shlex.split(command_template)]
    except KeyError as exc:
        raise ValueError(f"Unknown generator placeholder: {exc.args[0]}") from exc
    if not command:
        raise ValueError("Generator command is empty")
    completed = subprocess.run(command, cwd=context.run_dir, capture_output=True, text=True, check=False)
    if completed.returncode:
        context.fail_node("generate_report", "failed", "GeneratorError", returncode=completed.returncode)
        raise RuntimeError(f"Generator failed with exit code {completed.returncode}")
    guard_and_promote_report(context, report_path, provenance_path)


def adopt_report(context: RunContext, report: Path, provenance: Path) -> None:
    guard_and_promote_report(context, report, provenance)


def wait_for_generation(context: RunContext) -> int:
    entry = context.state.setdefault("nodes", {}).setdefault("generate_report", {})
    entry["status"] = "waiting_for_input"
    context.event("generate_report", "waiting_for_input", request_path=context.state["request_path"])
    context.checkpoint("waiting_for_generation")
    print(f"WAITING_FOR_GENERATION: {context.state['request_path']}")
    return WAITING_FOR_GENERATION


def validate_and_finalize(context: RunContext) -> int:
    report = Path(context.state["report_path"])
    final_report = Path(context.state["final_report_path"])
    if (
        context.node_status("finalize") in SUCCESS_STATUSES
        and final_report.is_file()
        and context.state.get("report_sha256") == sha256(final_report)
    ):
        print(f"COMPLETED: {final_report}")
        return 0
    if context.node_status("generate_report") not in SUCCESS_STATUSES or not report.is_file():
        return wait_for_generation(context)

    context.begin_node("validate_report")
    started = time.monotonic()
    generate_state = context.state.get("nodes", {}).get("generate_report", {})
    promoted_provenance = Path(context.state["provenance_path"])
    artifact_hashes_valid = (
        generate_state.get("output_sha256") == sha256(report)
        and promoted_provenance.is_file()
        and generate_state.get("provenance_sha256") == sha256(promoted_provenance)
    )
    if not artifact_hashes_valid:
        guard_path = context.run_dir / "guards" / "validate_report.json"
        guard = result_for_command(
            "validate_report",
            report,
            False,
            "PROMOTED_ARTIFACT_HASH_CHANGED",
        )
        write_guard_result(guard_path, guard)
        context.fail_node(
            "validate_report",
            "guard_failed",
            "ArtifactHashError",
            guard_path=str(guard_path),
        )
        return 1
    command = [sys.executable, str(ROOT / "scripts" / "validate_report.py"), str(report)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    validation = {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "report_sha256": sha256(report),
    }
    validation_path = context.run_dir / "validation.json"
    write_json(validation_path, validation)
    guard_path = context.run_dir / "guards" / "validate_report.json"
    guard = result_for_command(
        "validate_report",
        report,
        completed.returncode == 0,
        "HTML_VALIDATION_FAILED",
        {"returncode": completed.returncode},
    )
    write_guard_result(guard_path, guard)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if not guard.passed:
        context.fail_node(
            "validate_report",
            "guard_failed",
            "ValidationError",
            guard_path=str(guard_path),
            returncode=completed.returncode,
        )
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        return 1
    context.finish_node(
        "validate_report",
        guard_path=guard_path,
        output_path=validation_path,
        duration_ms=elapsed_ms,
        report_sha256=sha256(report),
    )

    eval_case_path = context.state.get("eval_case_path")
    if eval_case_path:
        context.begin_node("evaluate_report")
        eval_started = time.monotonic()
        evaluation = evaluate_path(report, load_case(Path(eval_case_path)))
        evaluation["report_sha256"] = sha256(report)
        evaluation_path = context.run_dir / "evaluation.json"
        write_json(evaluation_path, evaluation)
        eval_guard_path = context.run_dir / "guards" / "evaluate_report.json"
        eval_guard = result_for_command(
            "evaluate_report",
            evaluation_path,
            bool(evaluation["passed"]),
            "EVALUATION_FAILED",
            {"error_count": int(evaluation["error_count"])},
        )
        write_guard_result(eval_guard_path, eval_guard)
        eval_elapsed_ms = round((time.monotonic() - eval_started) * 1000)
        if not eval_guard.passed:
            context.fail_node(
                "evaluate_report",
                "guard_failed",
                "EvaluationError",
                guard_path=str(eval_guard_path),
                error_count=evaluation["error_count"],
            )
            print(f"EVALUATION_FAILED: {evaluation_path}", file=sys.stderr)
            return 1
        context.finish_node(
            "evaluate_report",
            guard_path=eval_guard_path,
            output_path=evaluation_path,
            duration_ms=eval_elapsed_ms,
            error_count=0,
            report_sha256=sha256(report),
        )
    else:
        eval_guard_path = context.run_dir / "guards" / "evaluate_report.json"
        eval_guard = result_for_command("evaluate_report", report, True, "EVALUATION_FAILED")
        eval_guard.counts["skipped"] = 1
        write_guard_result(eval_guard_path, eval_guard)
        context.finish_node("evaluate_report", status="skipped", guard_path=eval_guard_path)

    context.begin_node("finalize")
    finalize_started = time.monotonic()
    provenance = Path(context.state["provenance_path"])
    final_provenance = Path(context.state["final_provenance_path"])
    required_nodes = ("prepare", "generate_report", "validate_report", "evaluate_report")
    incomplete_nodes = [node_id for node_id in required_nodes if context.node_status(node_id) not in SUCCESS_STATUSES]
    artifact_hashes_valid = (
        generate_state.get("output_sha256") == sha256(report)
        and provenance.is_file()
        and generate_state.get("provenance_sha256") == sha256(provenance)
    )
    finalize_guard = result_for_command(
        "finalize",
        report,
        not incomplete_nodes and artifact_hashes_valid,
        "UPSTREAM_GUARD_INCOMPLETE",
        {"incomplete_count": len(incomplete_nodes), "artifact_hashes_valid": artifact_hashes_valid},
    )
    finalize_guard_path = context.run_dir / "guards" / "finalize.json"
    write_guard_result(finalize_guard_path, finalize_guard)
    if not finalize_guard.passed:
        context.fail_node(
            "finalize",
            "guard_failed",
            "FinalizeError",
            guard_path=str(finalize_guard_path),
            incomplete_nodes=incomplete_nodes,
        )
        return 1
    atomic_copy(report, final_report)
    atomic_copy(provenance, final_provenance)
    finalize_elapsed_ms = round((time.monotonic() - finalize_started) * 1000)
    context.finish_node(
        "finalize",
        guard_path=finalize_guard_path,
        output_path=final_report,
        duration_ms=finalize_elapsed_ms,
        final_provenance_path=str(final_provenance),
        final_provenance_sha256=sha256(final_provenance),
    )
    context.checkpoint("completed", report_sha256=sha256(final_report))
    print(f"COMPLETED: {final_report}")
    return 0


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
        "schema_version": 2,
        "run_id": run_id,
        "status": "created",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "skill_version": skill_version(),
        "job_path": str(job_path),
        "completed_steps": [],
        "nodes": {node_id: {"status": "pending"} for node_id in RUNTIME_DAG.topological_order()},
    }
    context = RunContext(run_dir, state)
    context.checkpoint("created")
    try:
        prepare_request(context, job_path, job, args.semantic_guard_command)
        if args.generator_command:
            run_generator(context, args.generator_command)
        return validate_and_finalize(context)
    except GuardFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        context.event("pipeline", "failed", error_type=type(exc).__name__)
        context.checkpoint("failed", error_type=type(exc).__name__)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def resume(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        print(f"ERROR: Missing state file: {state_path}", file=sys.stderr)
        return 2
    context = RunContext(run_dir, json.loads(state_path.read_text(encoding="utf-8")))
    if args.semantic_guard_command:
        context.checkpoint(semantic_guard_command=args.semantic_guard_command)
    try:
        if args.report:
            provenance = args.provenance
            if provenance is None:
                inferred = Path(str(args.report) + ".provenance.json")
                provenance = inferred if inferred.is_file() else None
            if provenance is None:
                raise ValueError("--provenance is required unless <report>.provenance.json exists")
            adopt_report(context, args.report, provenance)
        elif args.generator_command and context.node_status("generate_report") not in SUCCESS_STATUSES:
            run_generator(context, args.generator_command)
        return validate_and_finalize(context)
    except GuardFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        context.event("pipeline", "failed", error_type=type(exc).__name__)
        context.checkpoint("failed", error_type=type(exc).__name__)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def show_plan(args: argparse.Namespace) -> int:
    graph = RUNTIME_DAG if args.runtime else CONTENT_DAG
    rows = graph.describe()
    if args.json:
        print(json.dumps({"schema_version": 1, "nodes": rows}, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            dependencies = ",".join(row["depends_on"]) or "-"
            guards = ",".join(row["guards"])
            print(f"{row['node_id']}: depends_on={dependencies}; guards={guards}")
    return 0


def show_status(args: argparse.Namespace) -> int:
    state_path = args.run_dir.expanduser().resolve() / "state.json"
    if not state_path.is_file():
        print(f"ERROR: Missing state file: {state_path}", file=sys.stderr)
        return 2
    state = migrate_state(json.loads(state_path.read_text(encoding="utf-8")))
    safe = {
        "schema_version": state["schema_version"],
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "nodes": {
            node_id: {
                key: value
                for key, value in metadata.items()
                if key in {"status", "attempt", "started_at", "completed_at", "error_type", "finding_codes"}
            }
            for node_id, metadata in state.get("nodes", {}).items()
        },
    }
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Create a guarded run and prepare generation")
    start_parser.add_argument("--job", type=Path, required=True)
    start_parser.add_argument("--run-dir", type=Path)
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--generator-command")
    start_parser.add_argument("--semantic-guard-command")
    start_parser.set_defaults(handler=start)

    resume_parser = subparsers.add_parser("resume", help="Adopt guarded artifacts and continue a run")
    resume_parser.add_argument("--run-dir", type=Path, required=True)
    resume_parser.add_argument("--report", type=Path)
    resume_parser.add_argument("--provenance", type=Path)
    resume_parser.add_argument("--generator-command")
    resume_parser.add_argument("--semantic-guard-command")
    resume_parser.set_defaults(handler=resume)

    plan_parser = subparsers.add_parser("plan", help="Print the declared content DAG")
    plan_parser.add_argument("--runtime", action="store_true", help="Show the currently executable coarse DAG")
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.set_defaults(handler=show_plan)

    status_parser = subparsers.add_parser("status", help="Print privacy-safe run status")
    status_parser.add_argument("--run-dir", type=Path, required=True)
    status_parser.set_defaults(handler=show_status)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
