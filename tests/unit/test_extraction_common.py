from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from extraction_common import (  # noqa: E402
    ExtractedBlock,
    fenced_text,
    render_markdown,
    write_markdown,
    yaml_scalar,
)


class ExtractionCommonTests(unittest.TestCase):
    def test_yaml_scalar_supports_common_values(self) -> None:
        self.assertEqual(yaml_scalar(None), "null")
        self.assertEqual(yaml_scalar(True), "true")
        self.assertEqual(yaml_scalar(False), "false")
        self.assertEqual(yaml_scalar(12), "12")
        self.assertEqual(yaml_scalar("中英文"), '"中英文"')

    def test_fenced_text_uses_a_longer_fence_than_content(self) -> None:
        rendered = fenced_text("before ``` after")
        self.assertTrue(rendered.startswith("````text\n"))
        self.assertTrue(rendered.endswith("\n````"))

    def test_render_markdown_filters_empty_blocks_and_preserves_attributes(self) -> None:
        markdown = render_markdown(
            source_path=Path("/private/resume.docx"),
            source_type="docx",
            extraction_method="ooxml",
            blocks=[
                ExtractedBlock("DOCX-p0001", "paragraph", "Engineer", (("style", "Heading 1"),)),
                ExtractedBlock("DOCX-p0002", "paragraph", "   "),
            ],
            warnings=["Visual check required"],
            metadata={"page_count": None, "needs_visual_check": True},
        )

        self.assertIn('source_file: "resume.docx"', markdown)
        self.assertIn("extracted_blocks: 1", markdown)
        self.assertIn("page_count: null", markdown)
        self.assertIn("needs_visual_check: true", markdown)
        self.assertIn("style: Heading 1", markdown)
        self.assertNotIn("DOCX-p0002", markdown)

    def test_render_markdown_marks_an_empty_extraction(self) -> None:
        markdown = render_markdown(
            source_path=Path("empty.pdf"),
            source_type="pdf",
            extraction_method="pypdf",
            blocks=[],
            warnings=[],
            metadata={},
        )
        self.assertIn("warnings: []", markdown)
        self.assertIn("[No extractable text found.]", markdown)

    def test_write_markdown_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "resume.md"
            write_markdown(output, "first", overwrite=False)
            self.assertEqual(output.read_text(encoding="utf-8"), "first")

            with self.assertRaises(FileExistsError):
                write_markdown(output, "second", overwrite=False)

            write_markdown(output, "second", overwrite=True)
            self.assertEqual(output.read_text(encoding="utf-8"), "second")


if __name__ == "__main__":
    unittest.main()
