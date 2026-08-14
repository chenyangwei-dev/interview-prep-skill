#!/usr/bin/env python3
"""Claim-level provenance primitives used by grounding guards."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


CLAIM_TYPES = {
    "source_fact",
    "derived_fact",
    "inference",
    "recommendation",
    "assumption",
    "knowledge",
    "unknown",
}
EVIDENCE_ID_PATTERN = re.compile(r"^(?:JD|CV|USER|SRC)-\d{2,}$")
SOURCE_COMMENT = re.compile(r"<!--\s*source:\s*([^|>]+?)(?:\s*\|[^>]*)?-->")
FENCE_START = re.compile(r"(`{3,})text\s*$")
NUMBER_PATTERN = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?(?![\w.])")
STRONG_CONTRIBUTION = re.compile(
    r"主导|独立完成|全面负责|负责人|领导了|\bled\b|\bowned\b|\bdrove\b|\bmanaged\b",
    re.IGNORECASE,
)
WEAK_CONTRIBUTION = re.compile(
    r"参与|协助|支持|贡献于|\bparticipated\b|\bcontributed\b|\bassisted\b|\bsupported\b",
    re.IGNORECASE,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


@dataclass(frozen=True)
class SourceBlock:
    locator: str
    text: str


class SourceIndex:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.document_sha256 = sha256_path(self.path)
        raw = self.path.read_text(encoding="utf-8")
        self.blocks = self._parse_blocks(raw)

    @staticmethod
    def _parse_blocks(raw: str) -> dict[str, SourceBlock]:
        lines = raw.splitlines()
        blocks: dict[str, SourceBlock] = {}
        pending_locator: str | None = None
        index = 0
        while index < len(lines):
            source_match = SOURCE_COMMENT.search(lines[index])
            if source_match:
                pending_locator = source_match.group(1).strip()
                index += 1
                continue
            fence_match = FENCE_START.fullmatch(lines[index].strip())
            if pending_locator and fence_match:
                fence = fence_match.group(1)
                content: list[str] = []
                index += 1
                while index < len(lines) and lines[index].strip() != fence:
                    content.append(lines[index])
                    index += 1
                text = "\n".join(content).rstrip()
                if pending_locator in blocks:
                    raise ValueError(f"Duplicate source locator: {pending_locator}")
                blocks[pending_locator] = SourceBlock(pending_locator, text)
                pending_locator = None
            index += 1
        if not blocks:
            blocks["document"] = SourceBlock("document", raw)
        return blocks

    def span(self, locator: str, start: int, end: int) -> str:
        block = self.blocks.get(locator)
        if block is None:
            raise KeyError(f"Unknown source locator: {locator}")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
            raise ValueError("Invalid source span bounds")
        if end > len(block.text):
            raise ValueError("Source span exceeds block length")
        return block.text[start:end]


def validate_claim_shape(claim: dict[str, Any]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    claim_id = claim.get("claim_id")
    claim_type = claim.get("claim_type")
    text = claim.get("text")
    if not isinstance(claim_id, str) or not claim_id.strip():
        findings.append(("CLAIM_ID_MISSING", "claim_id is required"))
    if claim_type not in CLAIM_TYPES:
        findings.append(("CLAIM_TYPE_INVALID", f"invalid claim_type: {claim_type}"))
    if not isinstance(text, str) or not text.strip():
        findings.append(("CLAIM_TEXT_MISSING", "claim text is required"))

    refs = claim.get("evidence_refs", [])
    basis = claim.get("basis_claim_ids", [])
    if not isinstance(refs, list):
        findings.append(("EVIDENCE_REFS_INVALID", "evidence_refs must be a list"))
        refs = []
    if not isinstance(basis, list) or any(not isinstance(item, str) for item in basis):
        findings.append(("BASIS_CLAIMS_INVALID", "basis_claim_ids must be a list of IDs"))
        basis = []

    if claim_type == "source_fact" and not refs:
        findings.append(("SOURCE_FACT_WITHOUT_EVIDENCE", "source_fact requires evidence_refs"))
    if claim_type in {"derived_fact", "inference"} and not basis:
        findings.append(("DERIVED_CLAIM_WITHOUT_BASIS", f"{claim_type} requires basis_claim_ids"))
    if claim_type == "recommendation" and not basis and not claim.get("policy_refs"):
        findings.append(("RECOMMENDATION_WITHOUT_BASIS", "recommendation requires basis or policy_refs"))
    if claim_type == "assumption" and not claim.get("scope"):
        findings.append(("ASSUMPTION_WITHOUT_SCOPE", "assumption requires scope"))
    if claim_type == "unknown" and not claim.get("missing_fields"):
        findings.append(("UNKNOWN_WITHOUT_MISSING_FIELDS", "unknown requires missing_fields"))
    return findings


def contribution_escalated(claim_text: str, evidence_text: str) -> bool:
    return bool(
        STRONG_CONTRIBUTION.search(claim_text)
        and WEAK_CONTRIBUTION.search(evidence_text)
        and not STRONG_CONTRIBUTION.search(evidence_text)
    )


def numeric_conflicts(claim_text: str, evidence_text: str) -> set[str]:
    claim_numbers = set(NUMBER_PATTERN.findall(claim_text))
    evidence_numbers = set(NUMBER_PATTERN.findall(evidence_text))
    return claim_numbers - evidence_numbers


def replay_derived_fact(claim: dict[str, Any]) -> tuple[bool, str | None]:
    """Replay a small, non-executable formula schema used by derived claims."""
    formula = claim.get("formula")
    inputs = claim.get("inputs")
    expected = claim.get("value")
    if not isinstance(formula, dict) or not isinstance(inputs, list) or expected is None:
        return False, "schema"
    operation = formula.get("operation")
    precision = formula.get("precision")
    if precision is not None and (not isinstance(precision, int) or precision < 0 or precision > 12):
        return False, "precision"
    try:
        if operation == "count":
            computed = Decimal(len(inputs))
        else:
            numbers = [Decimal(str(item)) for item in inputs]
            if not numbers:
                return False, "inputs"
            if operation == "sum":
                computed = sum(numbers, Decimal(0))
            elif operation == "difference":
                computed = numbers[0] - sum(numbers[1:], Decimal(0))
            elif operation == "product":
                computed = Decimal(1)
                for number in numbers:
                    computed *= number
            elif operation in {"ratio", "percentage"} and len(numbers) == 2 and numbers[1] != 0:
                computed = numbers[0] / numbers[1]
                if operation == "percentage":
                    computed *= 100
            else:
                return False, "operation"
        if precision is not None:
            computed = round(computed, precision)
        return computed == Decimal(str(expected)), None
    except (InvalidOperation, TypeError, ValueError):
        return False, "value"
