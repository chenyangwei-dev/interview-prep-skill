from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract_docx  # noqa: E402


def write_complex_docx(path: Path) -> None:
    document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Experience</w:t></w:r></w:p>
    <w:p><w:ins><w:r><w:t>Current role</w:t></w:r></w:ins><w:del><w:r><w:delText>Old role</w:delText></w:r></w:del></w:p>
    <w:p><w:r><w:drawing/><w:t>Diagram caption</w:t></w:r></w:p>
    <w:p><w:r><w:txbxContent><w:p><w:r><w:t>Text box</w:t></w:r></w:p></w:txbxContent></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Python</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    <w:sectPr/>
  </w:body>
</w:document>"""
    styles = """<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/></w:style>
</w:styles>"""
    header = """<?xml version="1.0" encoding="UTF-8"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>Header text</w:t></w:r></w:p>
</w:hdr>"""
    footer = """<?xml version="1.0" encoding="UTF-8"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>Footer text</w:t></w:r></w:p>
</w:ftr>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/header1.xml", header)
        archive.writestr("word/footer1.xml", footer)
        archive.writestr("word/comments.xml", "<comments/>")


class ExtractDocxTests(unittest.TestCase):
    def test_text_from_preserves_tabs_and_breaks_but_excludes_deleted_text(self) -> None:
        root = ET.fromstring(
            f"""<w:p xmlns:w="{extract_docx.W_NS}">
            <w:r><w:t>Java</w:t><w:tab/><w:t>Python</w:t><w:br/><w:t>Go</w:t></w:r>
            <w:del><w:r><w:delText>Removed</w:delText></w:r></w:del>
            </w:p>"""
        )
        self.assertEqual(extract_docx.text_from(root), "Java\tPython\nGo")

    def test_build_markdown_extracts_structure_and_emits_risk_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "resume.docx"
            write_complex_docx(source)
            markdown = extract_docx.build_markdown(source)

        self.assertIn("style: Heading 1", markdown)
        self.assertIn("Current role", markdown)
        self.assertNotIn("Old role", markdown)
        self.assertIn("DOCX-body-t01-r01-c01", markdown)
        self.assertIn("DOCX-header01-p0001", markdown)
        self.assertIn("DOCX-footer01-p0001", markdown)
        self.assertIn("drawing_objects: 1", markdown)
        self.assertIn("text_boxes: 1", markdown)
        self.assertIn("has_comments: true", markdown)
        self.assertIn("Tracked changes are present", markdown)

    def test_build_markdown_rejects_invalid_or_incomplete_docx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            invalid = temp / "invalid.docx"
            invalid.write_text("not a zip", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "valid OOXML"):
                extract_docx.build_markdown(invalid)

            incomplete = temp / "incomplete.docx"
            with zipfile.ZipFile(incomplete, "w") as archive:
                archive.writestr("placeholder.txt", "missing document.xml")
            with self.assertRaisesRegex(ValueError, "missing word/document.xml"):
                extract_docx.build_markdown(incomplete)

    def test_main_rejects_wrong_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "resume.zip"
            source.write_bytes(b"placeholder")
            with (
                patch.object(sys, "argv", ["extract_docx.py", str(source), "-o", "out.md"]),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(extract_docx.main(), 2)


if __name__ == "__main__":
    unittest.main()
