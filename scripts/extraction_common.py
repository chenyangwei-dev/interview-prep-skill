#!/usr/bin/env python3
"""Shared helpers for deterministic resume-to-Markdown extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ExtractedBlock:
    source: str
    kind: str
    text: str
    attributes: tuple[tuple[str, str], ...] = ()


def yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def fenced_text(text: str) -> str:
    longest = 0
    current = 0
    for character in text:
        if character == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text.rstrip()}\n{fence}"


def render_markdown(
    *,
    source_path: Path,
    source_type: str,
    extraction_method: str,
    blocks: Iterable[ExtractedBlock],
    warnings: list[str],
    metadata: dict[str, object],
) -> str:
    materialized = [block for block in blocks if block.text.strip()]
    character_count = sum(len(block.text) for block in materialized)
    frontmatter: list[str] = [
        "---",
        f"source_file: {yaml_scalar(source_path.name)}",
        f"source_type: {yaml_scalar(source_type)}",
        f"extraction_method: {yaml_scalar(extraction_method)}",
        f"extracted_blocks: {len(materialized)}",
        f"extracted_characters: {character_count}",
    ]
    for key, value in metadata.items():
        frontmatter.append(f"{key}: {yaml_scalar(value)}")
    if warnings:
        frontmatter.append("warnings:")
        frontmatter.extend(f"  - {yaml_scalar(warning)}" for warning in warnings)
    else:
        frontmatter.append("warnings: []")
    frontmatter.extend(["---", "", "# Extracted resume content", ""])

    body: list[str] = []
    for block in materialized:
        attributes = [f"source: {block.source}", f"type: {block.kind}"]
        attributes.extend(f"{key}: {value}" for key, value in block.attributes if value)
        body.extend(
            [
                f"<!-- {' | '.join(attributes)} -->",
                "",
                fenced_text(block.text),
                "",
            ]
        )

    if not body:
        body.extend(["[No extractable text found.]", ""])
    return "\n".join(frontmatter + body).rstrip() + "\n"


def write_markdown(output_path: Path, content: str, overwrite: bool) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}. Pass --overwrite to replace it."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
