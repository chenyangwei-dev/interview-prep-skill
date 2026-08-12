#!/usr/bin/env python3
"""Extract a DOCX resume into source-located intermediate Markdown."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from extraction_common import ExtractedBlock, render_markdown, write_markdown


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
NS = {"w": W_NS}


def read_xml(archive: zipfile.ZipFile, member: str) -> ET.Element | None:
    try:
        data = archive.read(member)
    except KeyError:
        return None
    return ET.fromstring(data)


def text_from(element: ET.Element) -> str:
    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in {f"{W}br", f"{W}cr"}:
            parts.append("\n")
    text = "".join(parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def style_names(archive: zipfile.ZipFile) -> dict[str, str]:
    root = read_xml(archive, "word/styles.xml")
    if root is None:
        return {}
    names: dict[str, str] = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(f"{W}styleId", "")
        name = style.find("w:name", NS)
        if style_id and name is not None:
            names[style_id] = name.get(f"{W}val", style_id)
    return names


def paragraph_style(paragraph: ET.Element, names: dict[str, str]) -> str:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    if style is None:
        return ""
    style_id = style.get(f"{W}val", "")
    return names.get(style_id, style_id)


def extract_container(
    root: ET.Element,
    *,
    prefix: str,
    names: dict[str, str],
) -> list[ExtractedBlock]:
    blocks: list[ExtractedBlock] = []
    paragraph_index = 0
    table_index = 0
    body = root.find("w:body", NS)
    container = body if body is not None else root

    for child in list(container):
        if child.tag == f"{W}p":
            paragraph_index += 1
            text = text_from(child)
            style = paragraph_style(child, names)
            attributes = (("style", style),) if style else ()
            blocks.append(
                ExtractedBlock(
                    source=f"{prefix}-p{paragraph_index:04d}",
                    kind="paragraph",
                    text=text,
                    attributes=attributes,
                )
            )
        elif child.tag == f"{W}tbl":
            table_index += 1
            for row_index, row in enumerate(child.findall("w:tr", NS), start=1):
                for column_index, cell in enumerate(row.findall("w:tc", NS), start=1):
                    paragraphs = [text_from(p) for p in cell.findall("w:p", NS)]
                    text = "\n".join(part for part in paragraphs if part)
                    blocks.append(
                        ExtractedBlock(
                            source=(
                                f"{prefix}-t{table_index:02d}-r{row_index:02d}-c{column_index:02d}"
                            ),
                            kind="table_cell",
                            text=text,
                        )
                    )
    return blocks


def build_markdown(path: Path) -> str:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("DOCX is not a valid OOXML ZIP archive.") from exc

    with archive:
        document = read_xml(archive, "word/document.xml")
        if document is None:
            raise ValueError("DOCX is missing word/document.xml.")
        names = style_names(archive)
        blocks = extract_container(document, prefix="DOCX-body", names=names)

        header_members = sorted(
            name for name in archive.namelist() if re.fullmatch(r"word/header\d+\.xml", name)
        )
        footer_members = sorted(
            name for name in archive.namelist() if re.fullmatch(r"word/footer\d+\.xml", name)
        )
        for index, member in enumerate(header_members, start=1):
            root = read_xml(archive, member)
            if root is not None:
                blocks.extend(extract_container(root, prefix=f"DOCX-header{index:02d}", names=names))
        for index, member in enumerate(footer_members, start=1):
            root = read_xml(archive, member)
            if root is not None:
                blocks.extend(extract_container(root, prefix=f"DOCX-footer{index:02d}", names=names))

        drawing_count = len(document.findall(".//w:drawing", NS))
        text_box_count = len(document.findall(".//w:txbxContent", NS))
        tracked_insertions = len(document.findall(".//w:ins", NS))
        tracked_deletions = len(document.findall(".//w:del", NS))
        has_comments = "word/comments.xml" in archive.namelist()

    warnings = [
        "DOCX paragraph order does not prove visual reading order; render and visually inspect every page before creating evidence IDs."
    ]
    if drawing_count:
        warnings.append(f"Document contains {drawing_count} drawing object(s); image-only text may be missing.")
    if text_box_count:
        warnings.append(f"Document contains {text_box_count} text box(es); verify their reading order visually.")
    if tracked_insertions or tracked_deletions:
        warnings.append(
            "Tracked changes are present; inserted text is included, deleted text is excluded, and the result requires review."
        )
    if has_comments:
        warnings.append("Comments are present but are not included as resume evidence.")

    return render_markdown(
        source_path=path,
        source_type="docx",
        extraction_method="ooxml",
        blocks=blocks,
        warnings=warnings,
        metadata={
            "page_count": None,
            "body_blocks": sum(1 for block in blocks if block.source.startswith("DOCX-body")),
            "header_parts": len(header_members),
            "footer_parts": len(footer_members),
            "drawing_objects": drawing_count,
            "text_boxes": text_box_count,
            "tracked_insertions": tracked_insertions,
            "tracked_deletions": tracked_deletions,
            "has_comments": has_comments,
            "needs_visual_check": True,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source DOCX; it is never modified")
    parser.add_argument("--output", "-o", required=True, type=Path, help="Intermediate .md output")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        print(f"ERROR: Input does not exist: {args.input}", file=sys.stderr)
        return 2
    if args.input.suffix.lower() != ".docx":
        print("ERROR: Input must use the .docx extension.", file=sys.stderr)
        return 2
    if args.output.suffix.lower() != ".md":
        print("ERROR: Output must use the .md extension.", file=sys.stderr)
        return 2
    try:
        markdown = build_markdown(args.input)
        write_markdown(args.output, markdown, args.overwrite)
    except (FileExistsError, ValueError, ET.ParseError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"EXTRACTED: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
