from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract_pdf  # noqa: E402


class ExtractPdfTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("reportlab")
        and importlib.util.find_spec("pdfplumber")
        and importlib.util.find_spec("pypdf"),
        "PDF test dependencies are unavailable",
    )
    def test_both_pdf_engines_extract_text(self) -> None:
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "resume.pdf"
            document = canvas.Canvas(str(source))
            document.drawString(72, 720, "Distributed Systems Engineer")
            document.save()

            plumber_pages, plumber_method = extract_pdf.extract_with_pdfplumber(source)
            pypdf_pages, pypdf_method = extract_pdf.extract_with_pypdf(source)

        self.assertEqual(plumber_method, "pdfplumber")
        self.assertEqual(pypdf_method, "pypdf")
        self.assertIn("Distributed Systems Engineer", plumber_pages[0])
        self.assertIn("Distributed Systems Engineer", pypdf_pages[0])

    def test_extract_pages_falls_back_after_missing_dependency(self) -> None:
        missing = ImportError("missing pdfplumber", name="pdfplumber")
        with (
            patch.object(extract_pdf, "extract_with_pdfplumber", side_effect=missing),
            patch.object(extract_pdf, "extract_with_pypdf", return_value=(["page"], "pypdf")),
        ):
            pages, method, warnings = extract_pdf.extract_pages(Path("resume.pdf"))

        self.assertEqual(pages, ["page"])
        self.assertEqual(method, "pypdf")
        self.assertIn("dependency unavailable (pdfplumber)", warnings[0])

    def test_extract_pages_reports_all_failures(self) -> None:
        with (
            patch.object(extract_pdf, "extract_with_pdfplumber", side_effect=ValueError("broken")),
            patch.object(extract_pdf, "extract_with_pypdf", side_effect=ValueError("also broken")),
        ):
            with self.assertRaisesRegex(RuntimeError, "PDF extraction failed"):
                extract_pdf.extract_pages(Path("resume.pdf"))

    def test_build_markdown_marks_textless_pages_for_ocr_review(self) -> None:
        with patch.object(
            extract_pdf,
            "extract_pages",
            return_value=(["Readable page", ""], "pdfplumber", []),
        ):
            markdown = extract_pdf.build_markdown(Path("resume.pdf"))

        self.assertIn("page_count: 2", markdown)
        self.assertIn("textless_pages: 1", markdown)
        self.assertIn("needs_ocr_review: true", markdown)
        self.assertIn("PDF-p001", markdown)
        self.assertNotIn("PDF-p002", markdown)
        self.assertIn("No text was extracted from page(s): 2", markdown)

    def test_main_rejects_missing_input(self) -> None:
        with (
            patch.object(sys, "argv", ["extract_pdf.py", "missing.pdf", "-o", "out.md"]),
            redirect_stderr(StringIO()),
        ):
            self.assertEqual(extract_pdf.main(), 2)

    def test_main_validates_extensions_and_writes_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            wrong_input = temp / "resume.txt"
            wrong_input.write_text("text", encoding="utf-8")
            with (
                patch.object(sys, "argv", ["extract_pdf.py", str(wrong_input), "-o", "out.md"]),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(extract_pdf.main(), 2)

            source = temp / "resume.pdf"
            source.write_bytes(b"placeholder")
            with (
                patch.object(sys, "argv", ["extract_pdf.py", str(source), "-o", "out.txt"]),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(extract_pdf.main(), 2)

            output = temp / "out.md"
            with (
                patch.object(sys, "argv", ["extract_pdf.py", str(source), "-o", str(output)]),
                patch.object(extract_pdf, "build_markdown", return_value="# extracted\n"),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(extract_pdf.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "# extracted\n")


if __name__ == "__main__":
    unittest.main()
