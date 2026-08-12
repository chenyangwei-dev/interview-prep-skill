#!/usr/bin/env python3
"""Deterministic, privacy-aware checks for interview-preparation reports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


EVIDENCE_ID_PATTERN = re.compile(r"\b(?:JD|CV|USER)-\d{2}\b")
PLACEHOLDER_PATTERN = re.compile(r"\{\{[^{}]+\}\}|\[(?:待确认|To confirm)(?::[^\]]+)?\]")
PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "phone": re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    "cn_id": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            stripped = data.strip()
            if stripped:
                self.parts.append(stripped)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


def visible_text(raw: str, suffix: str = "") -> str:
    if suffix.lower() not in {".html", ".htm"}:
        return raw
    parser = _VisibleTextParser()
    parser.feed(raw)
    return "\n".join(parser.parts)


def load_case(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(f"Unsupported eval case schema in {path}")
    if not data.get("case_id"):
        raise ValueError(f"Missing case_id in {path}")
    return data


def evaluate_text(text: str, case: dict[str, Any]) -> dict[str, Any]:
    expected = case.get("expected", {})
    findings: list[Finding] = []
    allowed_ids = set(expected.get("allowed_evidence_ids", []))
    seen_ids = set(EVIDENCE_ID_PATTERN.findall(text))

    for evidence_id in sorted(set(expected.get("required_evidence_ids", [])) - seen_ids):
        findings.append(Finding("missing_evidence", "error", f"Missing evidence ID: {evidence_id}"))
    for evidence_id in sorted(seen_ids - allowed_ids):
        findings.append(Finding("unknown_evidence", "error", f"Unknown evidence ID: {evidence_id}"))

    for phrase in expected.get("required_phrases", []):
        if phrase not in text:
            findings.append(Finding("missing_phrase", "error", f"Missing required phrase: {phrase}"))
    for phrase in expected.get("forbidden_phrases", []):
        if phrase.casefold() in text.casefold():
            findings.append(Finding("forbidden_phrase", "error", f"Forbidden phrase found: {phrase}"))

    for fact in expected.get("numeric_facts", []):
        value = str(fact["value"])
        evidence_id = str(fact["evidence_id"])
        if value not in text:
            findings.append(Finding("missing_numeric_fact", "error", f"Missing numeric fact: {value}"))
        if evidence_id not in text:
            findings.append(Finding("unlinked_numeric_fact", "error", f"Numeric fact lacks evidence: {value} -> {evidence_id}"))

    for pattern_name, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding("pii_leak", "error", f"Possible {pattern_name} appears in report"))
    for value in case.get("private_test_values", []):
        if value and str(value) in text:
            findings.append(Finding("pii_leak", "error", "A case-specific private value appears in report"))

    unresolved = PLACEHOLDER_PATTERN.findall(text)
    allowed_unresolved = int(expected.get("max_unresolved_confirmations", 0))
    if len(unresolved) > allowed_unresolved:
        findings.append(
            Finding(
                "too_many_unresolved",
                "error",
                f"Found {len(unresolved)} unresolved markers; allowed {allowed_unresolved}",
            )
        )

    for pair in expected.get("bilingual_consistency", []):
        marker = str(pair["marker"])
        minimum = int(pair.get("minimum_occurrences", 2))
        if text.count(marker) < minimum:
            findings.append(
                Finding(
                    "bilingual_inconsistency",
                    "error",
                    f"Expected {minimum} occurrences of bilingual marker: {marker}",
                )
            )

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.code] = counts.get(finding.code, 0) + 1
    error_count = sum(finding.severity == "error" for finding in findings)
    warning_count = sum(finding.severity == "warning" for finding in findings)
    return {
        "case_id": case["case_id"],
        "passed": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "counts": counts,
        "observed": {
            "evidence_ids": sorted(seen_ids),
            "unresolved_markers": len(unresolved),
        },
        "findings": [asdict(finding) for finding in findings],
    }


def evaluate_path(report: Path, case: dict[str, Any]) -> dict[str, Any]:
    raw = report.read_text(encoding="utf-8")
    result = evaluate_text(visible_text(raw, report.suffix), case)
    result["report"] = str(report)
    return result


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
