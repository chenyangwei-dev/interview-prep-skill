#!/usr/bin/env python3
"""Extract a PDF resume into source-located intermediate Markdown."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from extraction_common import ExtractedBlock, render_markdown, write_markdown


def extract_with_pdfplumber(path: Path) -> tuple[list[str], str]:
    import pdfplumber  # type: ignore[import-not-found]

    with pdfplumber.open(path) as document:
        return [(page.extract_text() or "").strip() for page in document.pages], "pdfplumber"


def extract_with_pypdf(path: Path) -> tuple[list[str], str]:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:  # pragma: no cover - depends on encryption implementation.
            raise ValueError("PDF is encrypted and could not be opened.") from exc
        if not unlocked:
            raise ValueError("PDF is encrypted and requires a password.")
    return [(page.extract_text() or "").strip() for page in reader.pages], "pypdf"


def extract_pages(path: Path) -> tuple[list[str], str, list[str]]:
    failures: list[str] = []
    extractors = (
        ("pdfplumber", extract_with_pdfplumber),
        ("pypdf", extract_with_pypdf),
    )
    for name, extractor in extractors:
        try:
            pages, method = extractor(path)
            return pages, method, failures
        except ImportError as exc:
            failures.append(f"{name}: dependency unavailable ({exc.name})")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    raise RuntimeError("PDF extraction failed. " + "; ".join(failures))


def build_markdown(path: Path) -> str:
    pages, method, fallback_warnings = extract_pages(path)
    blocks = [
        ExtractedBlock(source=f"PDF-p{index:03d}", kind="page_text", text=text)
        for index, text in enumerate(pages, start=1)
    ]
    textless_pages = [index for index, text in enumerate(pages, start=1) if not text.strip()]
    warnings = list(fallback_warnings)
    warnings.append(
        "Text extraction does not preserve layout; render and visually inspect every page before creating evidence IDs."
    )
    if textless_pages:
        page_list = ", ".join(str(page) for page in textless_pages)
        warnings.append(f"No text was extracted from page(s): {page_list}; OCR review may be required.")
    needs_ocr_review = bool(pages) and len(textless_pages) * 2 >= len(pages)
    return render_markdown(
        source_path=path,
        source_type="pdf",
        extraction_method=method,
        blocks=blocks,
        warnings=warnings,
        metadata={
            "page_count": len(pages),
            "textless_pages": len(textless_pages),
            "needs_ocr_review": needs_ocr_review,
            "needs_visual_check": True,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source PDF; it is never modified")
    parser.add_argument("--output", "-o", required=True, type=Path, help="Intermediate .md output")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        print(f"ERROR: Input does not exist: {args.input}", file=sys.stderr)
        return 2
    if args.input.suffix.lower() != ".pdf":
        print("ERROR: Input must use the .pdf extension.", file=sys.stderr)
        return 2
    if args.output.suffix.lower() != ".md":
        print("ERROR: Output must use the .md extension.", file=sys.stderr)
        return 2
    try:
        markdown = build_markdown(args.input)
        write_markdown(args.output, markdown, args.overwrite)
    except (FileExistsError, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"EXTRACTED: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
