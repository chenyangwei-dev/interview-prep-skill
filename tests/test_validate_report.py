from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_report  # noqa: E402


class ValidateReportTests(unittest.TestCase):
    def test_bundled_templates_are_valid_when_placeholders_are_allowed(self) -> None:
        for name in ("interview-prep-template.zh.html", "interview-prep-template.en.html"):
            with self.subTest(name=name):
                errors, warnings = validate_report.validate(ROOT / "assets" / name, True)
                self.assertEqual(errors, [])
                self.assertEqual(warnings, [])

    def test_missing_file_returns_an_actionable_error(self) -> None:
        errors, warnings = validate_report.validate(Path("missing-report.html"), False)
        self.assertEqual(warnings, [])
        self.assertEqual(errors, ["File does not exist: missing-report.html"])

    def test_invalid_utf8_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.html"
            report.write_bytes(b"\xff\xfe")
            errors, _ = validate_report.validate(report, False)
        self.assertEqual(errors, ["Report is not valid UTF-8."])

    def test_validator_detects_unsafe_and_incomplete_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.txt"
            report.write_text(
                """<!doctype html><html><head><title>Report</title>
                <script src="https://example.com/app.js"></script></head>
                <body onload="run()"><script>node.innerHTML = '{{unsafe}}'</script></body></html>""",
                encoding="utf-8",
            )
            errors, _ = validate_report.validate(report, False)

        combined = "\n".join(errors)
        self.assertIn("Report must use the .html extension", combined)
        self.assertIn("External asset dependencies are not allowed", combined)
        self.assertIn("Inline event handlers are not allowed", combined)
        self.assertIn("Do not use innerHTML", combined)
        self.assertIn("Unresolved template placeholders remain", combined)


if __name__ == "__main__":
    unittest.main()
