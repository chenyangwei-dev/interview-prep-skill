#!/usr/bin/env python3
"""Blocking guards for staged interview-prep artifacts."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

from provenance import (
    EVIDENCE_ID_PATTERN,
    SourceIndex,
    contribution_escalated,
    normalized_text,
    numeric_conflicts,
    replay_derived_fact,
    sha256_path,
    sha256_text,
    validate_claim_shape,
)


SEMANTIC_STATUSES = {"supported", "partially_supported", "unsupported", "ambiguous"}
FACT_LABELS = ("[JD事实", "[简历事实", "[用户确认", "[JD Fact", "[Resume Fact", "[User-confirmed")
TYPE_LABELS = {
    "derived_fact": ("[计算事实", "[Derived fact"),
    "inference": ("[推断", "[Inference"),
    "recommendation": ("[建议", "[Recommendation"),
    "assumption": ("[假设", "[Assumption"),
    "unknown": ("[待确认", "[To confirm"),
    "knowledge": ("[知识", "[Knowledge", "[建议", "[Recommendation"),
}
ALL_CLAIM_LABELS = tuple(dict.fromkeys(FACT_LABELS + tuple(label for labels in TYPE_LABELS.values() for label in labels)))
SOURCE_LABELS = {
    "jd": ("[JD事实", "[JD Fact"),
    "resume": ("[简历事实", "[Resume Fact"),
    "user": ("[用户确认", "[User-confirmed"),
}
GROUNDED_CLAIM_TYPES = {"source_fact", "knowledge"}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str = "block"
    claim_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class GuardResult:
    node_id: str
    artifact_sha256: str | None
    status: str
    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "passed" and not any(item.severity == "block" for item in self.findings)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "node_id": self.node_id,
            "artifact_sha256": self.artifact_sha256,
            "status": self.status,
            "counts": self.counts,
            "findings": [asdict(item) for item in self.findings],
        }


class GuardFailure(RuntimeError):
    def __init__(self, result: GuardResult) -> None:
        self.result = result
        codes = ", ".join(sorted({item.code for item in result.findings})) or "UNKNOWN"
        super().__init__(f"Guard failed for {result.node_id}: {codes}")


class _ClaimBindingParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active: list[tuple[str, str | None]] = []
        self.bindings: dict[str, list[str]] = {}
        self.unbound_claim_labels = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        inherited = self.active[-1][1] if self.active else None
        claim_id = attr_map.get("data-claim-id") or inherited
        if claim_id:
            self.bindings.setdefault(claim_id, [])
        if tag not in self.VOID_TAGS:
            self.active.append((tag, claim_id))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.active) - 1, -1, -1):
            if self.active[index][0] == tag:
                del self.active[index:]
                break

    def handle_data(self, data: str) -> None:
        claim_id = self.active[-1][1] if self.active else None
        if claim_id:
            self.bindings.setdefault(claim_id, []).append(data)
        elif any(label in data for label in ALL_CLAIM_LABELS):
            self.unbound_claim_labels += 1


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_guard_result(path: Path, result: GuardResult) -> None:
    _write_json(path, result.as_dict())


def guard_prepare_request(request: dict[str, Any]) -> GuardResult:
    findings: list[Finding] = []
    if request.get("schema_version") not in {1, 2}:
        findings.append(Finding("REQUEST_SCHEMA_UNSUPPORTED"))
    inputs = request.get("inputs")
    if not isinstance(inputs, dict):
        findings.append(Finding("REQUEST_INPUTS_MISSING"))
        inputs = {}
    required = {"jd", "resume"}
    for source_name in sorted(required | set(inputs)):
        metadata = inputs.get(source_name)
        if not isinstance(metadata, dict):
            if source_name in required:
                findings.append(Finding("NORMALIZED_INPUT_MISSING", metadata={"source": source_name}))
            else:
                findings.append(Finding("NORMALIZED_INPUT_METADATA_INVALID", metadata={"source": source_name}))
            continue
        raw_path = metadata.get("normalized_path")
        if not isinstance(raw_path, str):
            findings.append(Finding("NORMALIZED_PATH_MISSING", metadata={"source": source_name}))
            continue
        path = Path(raw_path)
        if not path.is_file() or path.stat().st_size == 0:
            findings.append(Finding("NORMALIZED_INPUT_INVALID", metadata={"source": source_name}))
            continue
        expected_hash = metadata.get("normalized_sha256")
        if expected_hash != sha256_path(path):
            findings.append(Finding("NORMALIZED_HASH_MISMATCH", metadata={"source": source_name}))
        try:
            SourceIndex(path)
        except (OSError, UnicodeError, ValueError) as exc:
            findings.append(
                Finding("NORMALIZED_SOURCE_INDEX_INVALID", metadata={"source": source_name, "error_type": type(exc).__name__})
            )
    status = "passed" if not findings else "failed"
    return GuardResult("prepare", None, status, findings, {"inputs": len(inputs)})


def _source_indexes(request: dict[str, Any]) -> tuple[dict[str, SourceIndex], list[Finding]]:
    indexes: dict[str, SourceIndex] = {}
    findings: list[Finding] = []
    inputs = request.get("inputs", {})
    for source_name in sorted(inputs):
        metadata = inputs.get(source_name, {})
        raw_path = metadata.get("normalized_path") if isinstance(metadata, dict) else None
        if not isinstance(raw_path, str):
            findings.append(Finding("NORMALIZED_PATH_MISSING", metadata={"source": source_name}))
            continue
        try:
            indexes[source_name] = SourceIndex(Path(raw_path))
        except (OSError, UnicodeError, ValueError) as exc:
            findings.append(
                Finding("SOURCE_INDEX_FAILED", metadata={"source": source_name, "error_type": type(exc).__name__})
            )
    return indexes, findings


def _expected_prefix(source: str) -> str | None:
    if source.startswith("src:"):
        return "SRC-"
    return {"jd": "JD-", "resume": "CV-", "user": "USER-"}.get(source)


def _source_labels(source: str) -> tuple[str, ...]:
    if source.startswith("src:"):
        return TYPE_LABELS["knowledge"]
    return SOURCE_LABELS.get(source, ())


def _label_present(claim_type: str, bound_text: str) -> bool:
    if claim_type == "source_fact":
        return any(label in bound_text for label in FACT_LABELS)
    labels = TYPE_LABELS.get(claim_type)
    return True if labels is None else any(label in bound_text for label in labels)


def guard_report(
    report: Path,
    manifest: Path,
    request: dict[str, Any],
    semantic_results: Mapping[str, str] | None = None,
) -> tuple[GuardResult, list[dict[str, object]]]:
    findings: list[Finding] = []
    ambiguous_requests: list[dict[str, object]] = []
    if not report.is_file():
        result = GuardResult("generate_report", None, "failed", [Finding("REPORT_MISSING")])
        return result, ambiguous_requests
    artifact_hash = sha256_path(report)
    if not manifest.is_file():
        result = GuardResult(
            "generate_report",
            artifact_hash,
            "failed",
            [Finding("PROVENANCE_MANIFEST_MISSING")],
        )
        return result, ambiguous_requests

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = GuardResult(
            "generate_report",
            artifact_hash,
            "failed",
            [Finding("PROVENANCE_MANIFEST_INVALID", metadata={"error_type": type(exc).__name__})],
        )
        return result, ambiguous_requests

    if data.get("schema_version") != 1:
        findings.append(Finding("PROVENANCE_SCHEMA_UNSUPPORTED"))
    if data.get("report_sha256") != artifact_hash:
        findings.append(Finding("REPORT_HASH_MISMATCH"))
    request_inputs = request.get("inputs", {})
    manifest_inputs = data.get("input_sha256")
    expected_inputs = {
        name: metadata.get("normalized_sha256")
        for name, metadata in request_inputs.items()
        if isinstance(metadata, dict)
    } if isinstance(request_inputs, dict) else {}
    if manifest_inputs != expected_inputs:
        findings.append(Finding("INPUT_HASH_SET_MISMATCH"))
    claims = data.get("claims")
    if not isinstance(claims, list):
        findings.append(Finding("CLAIMS_MISSING"))
        claims = []

    indexes, source_findings = _source_indexes(request)
    findings.extend(source_findings)
    report_text = report.read_text(encoding="utf-8")
    parser = _ClaimBindingParser()
    try:
        parser.feed(report_text)
    except Exception as exc:
        findings.append(Finding("CLAIM_BINDING_PARSE_FAILED", metadata={"error_type": type(exc).__name__}))

    by_id: dict[str, dict[str, Any]] = {}
    verified_spans: dict[str, list[str]] = {}
    duplicate_ids: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            findings.append(Finding("CLAIM_NOT_OBJECT"))
            continue
        claim_id = claim.get("claim_id")
        if isinstance(claim_id, str):
            if claim_id in by_id:
                duplicate_ids.add(claim_id)
            by_id[claim_id] = claim
        for code, _ in validate_claim_shape(claim):
            findings.append(Finding(code, claim_id=claim_id if isinstance(claim_id, str) else None))
    for claim_id in sorted(duplicate_ids):
        findings.append(Finding("CLAIM_ID_DUPLICATE", claim_id=claim_id))

    for claim_id in sorted(set(parser.bindings) - set(by_id)):
        findings.append(Finding("HTML_REFERENCES_UNKNOWN_CLAIM", claim_id=claim_id))
    if parser.unbound_claim_labels:
        findings.append(Finding("UNBOUND_CLAIM_LABEL", metadata={"count": parser.unbound_claim_labels}))
    if not by_id:
        findings.append(Finding("CLAIMS_EMPTY"))

    semantic_results = semantic_results or {}
    for claim_id, claim in by_id.items():
        bound_text = " ".join(parser.bindings.get(claim_id, []))
        if not bound_text.strip():
            findings.append(Finding("CLAIM_NOT_BOUND_IN_REPORT", claim_id=claim_id))
        elif normalized_text(str(claim.get("text", ""))) not in normalized_text(bound_text):
            findings.append(Finding("CLAIM_TEXT_NOT_BOUND", claim_id=claim_id))
        claim_type = str(claim.get("claim_type", ""))
        if bound_text and not _label_present(claim_type, bound_text):
            findings.append(Finding("CLAIM_LABEL_MISSING", claim_id=claim_id, metadata={"claim_type": claim_type}))

        for basis_id in claim.get("basis_claim_ids", []) if isinstance(claim.get("basis_claim_ids", []), list) else []:
            if basis_id not in by_id:
                findings.append(Finding("UNKNOWN_BASIS_CLAIM", claim_id=claim_id))

        spans: list[str] = []
        ref_sources: set[str] = set()
        refs = claim.get("evidence_refs", []) if isinstance(claim.get("evidence_refs", []), list) else []
        for ref in refs:
            if not isinstance(ref, dict):
                findings.append(Finding("EVIDENCE_REF_INVALID", claim_id=claim_id))
                continue
            evidence_id = ref.get("evidence_id")
            source = ref.get("source")
            source_name = str(source)
            if not isinstance(evidence_id, str) or not EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
                findings.append(Finding("EVIDENCE_ID_INVALID", claim_id=claim_id))
            expected_prefix = _expected_prefix(source_name)
            if expected_prefix is None:
                findings.append(Finding("EVIDENCE_SOURCE_INVALID", claim_id=claim_id))
            if expected_prefix and isinstance(evidence_id, str) and not evidence_id.startswith(expected_prefix):
                findings.append(Finding("EVIDENCE_SOURCE_MISMATCH", claim_id=claim_id))
            if source_name.startswith("src:") and isinstance(evidence_id, str) and source_name != f"src:{evidence_id}":
                findings.append(Finding("EVIDENCE_SOURCE_MISMATCH", claim_id=claim_id))
            index = indexes.get(source_name)
            if index is None:
                findings.append(Finding("EVIDENCE_SOURCE_UNAVAILABLE", claim_id=claim_id))
                continue
            if ref.get("source_document_sha256") != index.document_sha256:
                findings.append(Finding("SOURCE_DOCUMENT_HASH_MISMATCH", claim_id=claim_id))
                continue
            try:
                span = index.span(str(ref.get("locator")), ref.get("span_start"), ref.get("span_end"))
            except (KeyError, TypeError, ValueError):
                findings.append(Finding("SOURCE_SPAN_INVALID", claim_id=claim_id))
                continue
            if ref.get("span_sha256") != sha256_text(span):
                findings.append(Finding("SOURCE_SPAN_HASH_MISMATCH", claim_id=claim_id))
                continue
            spans.append(span)
            ref_sources.add(source_name)

        if claim_type in GROUNDED_CLAIM_TYPES and bound_text:
            for source_name in ref_sources:
                labels = _source_labels(source_name)
                if labels and not any(label in bound_text for label in labels):
                    findings.append(
                        Finding("CLAIM_SOURCE_LABEL_MISMATCH", claim_id=claim_id, metadata={"source": source_name})
                    )

        evidence_text = "\n".join(spans)
        verified_spans[claim_id] = spans
        claim_text = str(claim.get("text", ""))
        if claim_type in GROUNDED_CLAIM_TYPES and spans:
            if claim_type == "source_fact" and contribution_escalated(claim_text, evidence_text):
                findings.append(Finding("CONTRIBUTION_ESCALATION", claim_id=claim_id))
            conflicts = numeric_conflicts(claim_text, evidence_text)
            if conflicts:
                findings.append(Finding("NUMERIC_CONFLICT", claim_id=claim_id, metadata={"count": len(conflicts)}))

            if normalized_text(claim_text) in normalized_text(evidence_text):
                semantic_status = "supported"
            else:
                semantic_status = semantic_results.get(claim_id, "ambiguous")
                if semantic_status not in SEMANTIC_STATUSES:
                    semantic_status = "ambiguous"
                if claim_id not in semantic_results:
                    ambiguous_requests.append(
                        {"claim_id": claim_id, "claim": claim_text, "evidence_spans": spans}
                    )
            if semantic_status != "supported":
                findings.append(
                    Finding(
                        "SEMANTIC_" + semantic_status.upper(),
                        claim_id=claim_id,
                        severity="block",
                    )
                )
        if claim_type == "knowledge" and not refs:
            findings.append(Finding("KNOWLEDGE_WITHOUT_SOURCE", claim_id=claim_id))
        if claim_type == "derived_fact":
            replayed, reason = replay_derived_fact(claim)
            if not replayed:
                findings.append(
                    Finding("DERIVED_FACT_NOT_REPLAYABLE", claim_id=claim_id, metadata={"reason": reason or "mismatch"})
                )
            elif str(claim.get("value")) not in claim_text:
                findings.append(Finding("DERIVED_VALUE_NOT_RENDERED", claim_id=claim_id))

    def source_spans_for(claim_id: str, trail: set[str] | None = None) -> list[str]:
        trail = set() if trail is None else set(trail)
        if claim_id in trail:
            return []
        trail.add(claim_id)
        spans = list(verified_spans.get(claim_id, []))
        claim = by_id.get(claim_id, {})
        basis = claim.get("basis_claim_ids", []) if isinstance(claim.get("basis_claim_ids", []), list) else []
        for basis_id in basis:
            if isinstance(basis_id, str):
                spans.extend(source_spans_for(basis_id, trail))
        return spans

    for claim_id, claim in by_id.items():
        if claim.get("claim_type") != "inference":
            continue
        claim_text = str(claim.get("text", ""))
        basis_spans = source_spans_for(claim_id)
        if not basis_spans:
            findings.append(Finding("INFERENCE_WITHOUT_GROUNDED_BASIS", claim_id=claim_id))
            continue
        semantic_status = semantic_results.get(claim_id, "ambiguous")
        if semantic_status not in SEMANTIC_STATUSES:
            semantic_status = "ambiguous"
        if claim_id not in semantic_results:
            ambiguous_requests.append(
                {"claim_id": claim_id, "claim": claim_text, "evidence_spans": basis_spans}
            )
        if semantic_status != "supported":
            findings.append(Finding("SEMANTIC_" + semantic_status.upper(), claim_id=claim_id))

    blocking = [item for item in findings if item.severity == "block"]
    status = "passed" if not blocking else "failed"
    counts = {
        "claims": len(by_id),
        "findings": len(findings),
        "blocking": len(blocking),
        "ambiguous": sum(item.code == "SEMANTIC_AMBIGUOUS" for item in findings),
    }
    return GuardResult("generate_report", artifact_hash, status, findings, counts), ambiguous_requests


def run_semantic_checker(
    command_template: str,
    requests: list[dict[str, object]],
    run_dir: Path,
) -> dict[str, str]:
    if not requests:
        return {}
    request_path = run_dir / "staging" / "semantic-guard-request.json"
    output_path = run_dir / "staging" / "semantic-guard-result.json"
    _write_json(request_path, {"schema_version": 1, "claims": requests})
    output_path.unlink(missing_ok=True)
    replacements = {
        "request": str(request_path),
        "output": str(output_path),
        "run_dir": str(run_dir),
    }
    try:
        command = [argument.format(**replacements) for argument in shlex.split(command_template)]
    except KeyError as exc:
        raise ValueError(f"Unknown semantic guard placeholder: {exc.args[0]}") from exc
    if not command:
        raise ValueError("Semantic guard command is empty")
    completed = subprocess.run(command, cwd=run_dir, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"Semantic guard failed with exit code {completed.returncode}")
    if not output_path.is_file():
        raise RuntimeError("Semantic guard did not create its output file")
    data = json.loads(output_path.read_text(encoding="utf-8"))
    items = data.get("claims")
    if data.get("schema_version") != 1 or not isinstance(items, list):
        raise ValueError("Semantic guard output schema is invalid")
    results: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Semantic guard claim result must be an object")
        claim_id = item.get("claim_id")
        status = item.get("status")
        if not isinstance(claim_id, str) or status not in SEMANTIC_STATUSES:
            raise ValueError("Semantic guard claim result is invalid")
        results[claim_id] = status
    return results


def result_for_command(
    node_id: str,
    artifact: Path | None,
    passed: bool,
    failure_code: str,
    metadata: dict[str, object] | None = None,
) -> GuardResult:
    findings = [] if passed else [Finding(failure_code, metadata=metadata or {})]
    return GuardResult(
        node_id,
        sha256_path(artifact) if artifact and artifact.is_file() else None,
        "passed" if passed else "failed",
        findings,
        {"blocking": len(findings)},
    )
