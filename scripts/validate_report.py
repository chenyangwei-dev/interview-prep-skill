#!/usr/bin/env python3
"""Validate a generated interview-preparation HTML report."""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_SECTION_IDS = {
    "overview",
    "match",
    "risks",
    "introductions",
    "stories",
    "questions",
    "deep-dive",
    "reverse",
    "plan",
    "cheat-sheet",
    "evidence",
    "confirmations",
    "integrity",
}


class ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.ids: set[str] = set()
        self.title_parts: list[str] = []
        self.in_title = False
        self.html_lang = ""
        self.viewport = False
        self.charset = False
        self.external_assets: list[str] = []
        self.inline_handlers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        attr_map = {key.lower(): value or "" for key, value in attrs}
        element_id = attr_map.get("id")
        if element_id:
            self.ids.add(element_id)

        if tag == "html":
            self.html_lang = attr_map.get("lang", "").strip()
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            if attr_map.get("name", "").lower() == "viewport":
                self.viewport = True
            if "charset" in attr_map:
                self.charset = attr_map["charset"].lower() == "utf-8"

        for key, value in attr_map.items():
            if key.startswith("on"):
                self.inline_handlers.append(f"<{tag} {key}=...>")

        if tag in {"script", "img", "iframe", "audio", "video", "source"}:
            self._record_external(attr_map.get("src", ""), tag)
        if tag == "link":
            self._record_external(attr_map.get("href", ""), tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    def _record_external(self, value: str, tag: str) -> None:
        if not value:
            return
        scheme = urlparse(value).scheme.lower()
        if scheme in {"http", "https", "//"} or value.startswith("//"):
            self.external_assets.append(f"<{tag}>: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Path to the generated .html report")
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help="Allow {{...}} fields and template-only comments when validating bundled templates",
    )
    return parser.parse_args()


def validate(path: Path, allow_placeholders: bool) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        return [f"File does not exist: {path}"], warnings
    if path.suffix.lower() != ".html":
        errors.append("Report must use the .html extension.")

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["Report is not valid UTF-8."], warnings

    parser = ReportParser()
    try:
        parser.feed(text)
    except Exception as exc:  # HTMLParser failures are rare but should be actionable.
        errors.append(f"HTML parser failed: {exc}")

    if not re.match(r"\s*<!doctype\s+html", text, flags=re.IGNORECASE):
        errors.append("Missing <!doctype html> declaration.")
    if not parser.html_lang:
        errors.append("Missing lang attribute on <html>.")
    if not parser.charset:
        errors.append("Missing <meta charset=\"utf-8\">.")
    if not parser.viewport:
        errors.append("Missing viewport meta tag.")
    if not "".join(parser.title_parts).strip():
        errors.append("Page <title> is empty or missing.")

    required_tags = {"header", "nav", "main", "section", "table", "details", "summary", "button", "style", "script"}
    missing_tags = sorted(required_tags - parser.tags)
    if missing_tags:
        errors.append(f"Missing required semantic or interactive tags: {', '.join(missing_tags)}")

    missing_sections = sorted(REQUIRED_SECTION_IDS - parser.ids)
    if missing_sections:
        errors.append(f"Missing required section IDs: {', '.join(missing_sections)}")

    if parser.external_assets:
        errors.append("External asset dependencies are not allowed: " + "; ".join(parser.external_assets))
    if parser.inline_handlers:
        errors.append("Inline event handlers are not allowed: " + "; ".join(parser.inline_handlers))
    if re.search(r"\binnerHTML\b", text):
        errors.append("Do not use innerHTML; keep user content out of executable contexts.")
    if "@media print" not in text:
        errors.append("Missing print stylesheet (@media print).")
    if not re.search(r"@media\s*\([^)]*max-width", text):
        errors.append("Missing responsive max-width media query.")
    if "question-search" not in parser.ids:
        errors.append("Missing question search control with id=question-search.")
    if "expand-all" not in parser.ids or "collapse-all" not in parser.ids:
        errors.append("Missing expand-all or collapse-all Q&A control.")
    if "print-report" not in parser.ids:
        errors.append("Missing print-report control.")

    has_zh_labels = "[JD事实" in text and "[简历事实" in text
    has_en_labels = "[JD Fact" in text and "[Resume Fact" in text
    if not (has_zh_labels or has_en_labels):
        errors.append("Evidence label guide or labeled claims are missing.")

    placeholders = re.findall(r"\{\{[^{}]+\}\}", text)
    if placeholders and not allow_placeholders:
        sample = ", ".join(dict.fromkeys(placeholders[:5]))
        errors.append(f"Unresolved template placeholders remain ({len(placeholders)}): {sample}")

    template_markers = ("最终输出删除本注释", "remove this comment from the final output")
    if any(marker in text for marker in template_markers) and not allow_placeholders:
        errors.append("Template-only comments remain in the generated report.")
    if "```" in text:
        errors.append("Markdown code fences must not appear in the HTML report.")

    if not re.search(r"<div[^>]+class=[\"'][^\"']*summary-grid", text, flags=re.IGNORECASE):
        errors.append("Missing visual summary grid.")
    if not re.search(r"class=[\"'][^\"']*table-wrap", text, flags=re.IGNORECASE):
        errors.append("Tables must be wrapped for horizontal overflow.")

    if re.search(r"(?:match|匹配)[^\n<]{0,40}\b\d{1,3}%", text, flags=re.IGNORECASE):
        warnings.append("A match percentage was found; keep it only if the user supplied an explicit scoring rubric.")

    return errors, warnings


def main() -> int:
    args = parse_args()
    errors, warnings = validate(args.report, args.allow_placeholders)

    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"VALID: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
