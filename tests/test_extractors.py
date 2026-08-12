from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def write_docx(path: Path) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Chenyang Wei</w:t></w:r></w:p>
    <w:p><w:r><w:t>中英文 Resume</w:t></w:r></w:p>
    <w:tbl><w:tr>
      <w:tc><w:p><w:r><w:t>Role</w:t></w:r></w:p></w:tc>
      <w:tc><w:p><w:r><w:t>Engineer</w:t></w:r></w:p></w:tc>
    </w:tr></w:tbl>
    <w:sectPr/>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)


class ExtractorTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("reportlab") and importlib.util.find_spec("pdfplumber"),
        "PDF test dependencies are unavailable",
    )
    def test_pdf_to_markdown(self) -> None:
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "resume.pdf"
            output = temp / "resume.extracted.md"
            document = canvas.Canvas(str(source))
            document.drawString(72, 720, "Backend Engineer - Python and gRPC")
            document.save()

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "extract_pdf.py"), str(source), "-o", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            markdown = output.read_text(encoding="utf-8")
            self.assertIn('source_type: "pdf"', markdown)
            self.assertIn("PDF-p001", markdown)
            self.assertIn("Backend Engineer - Python and gRPC", markdown)

    def test_docx_to_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "resume.docx"
            output = temp / "resume.extracted.md"
            write_docx(source)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "extract_docx.py"), str(source), "-o", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            markdown = output.read_text(encoding="utf-8")
            self.assertIn('source_type: "docx"', markdown)
            self.assertIn("DOCX-body-p0001", markdown)
            self.assertIn("DOCX-body-t01-r01-c02", markdown)
            self.assertIn("Chenyang Wei", markdown)
            self.assertIn("中英文 Resume", markdown)

    def test_existing_output_requires_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "resume.docx"
            output = temp / "resume.extracted.md"
            write_docx(source)
            output.write_text("keep", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "extract_docx.py"), str(source), "-o", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
