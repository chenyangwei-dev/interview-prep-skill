from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_report  # noqa: E402


def generated_report() -> str:
    report = (ROOT / "assets" / "interview-prep-template.zh.html").read_text(encoding="utf-8")
    for kind, count in (("system-design", 2), ("management-interview", 6)):
        pattern = rf'(<details[^>]+data-kind="{re.escape(kind)}"[^>]*>.*?</details>)'
        match = re.search(pattern, report, flags=re.DOTALL)
        if not match:
            raise AssertionError(f"Missing template card for data-kind={kind}")
        report = report[: match.start()] + (match.group(1) * count) + report[match.end() :]
    report = re.sub(r"\{\{[^{}]+\}\}", "合成测试内容", report)
    return re.sub(r"<!--.*?最终输出删除本注释。.*?-->", "", report, flags=re.DOTALL)


def write_report(directory: str, text: str) -> Path:
    path = Path(directory) / "report.html"
    path.write_text(text, encoding="utf-8")
    return path


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

    def test_generated_report_accepts_valid_specialty_card_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            errors, warnings = validate_report.validate(write_report(directory, generated_report()), False)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_validator_rejects_missing_specialty_headings_and_cards(self) -> None:
        report = generated_report()
        report = re.sub(
            r'<section id="system-design">.*?</section>',
            '<section id="system-design"><h2>架构练习</h2></section>',
            report,
            flags=re.DOTALL,
        )
        report = re.sub(
            r'<section id="management-interview">.*?</section>',
            '<section id="management-interview"><h2>领导轮次</h2></section>',
            report,
            flags=re.DOTALL,
        )

        with tempfile.TemporaryDirectory() as directory:
            errors, _ = validate_report.validate(write_report(directory, report), False)

        combined = "\n".join(errors)
        self.assertIn("System-design section lacks a heading", combined)
        self.assertIn("System-design section must contain 2–3 details cards", combined)
        self.assertIn("Management-interview section lacks a recognizable heading", combined)
        self.assertIn("Management-interview section must contain 6–8 details cards", combined)

    def test_system_design_not_applicable_allows_zero_cards(self) -> None:
        report = re.sub(
            r'<section id="system-design">.*?</section>',
            '<section id="system-design"><h2>系统设计</h2><p>不适用：岗位没有相关信号。</p></section>',
            generated_report(),
            flags=re.DOTALL,
        )

        with tempfile.TemporaryDirectory() as directory:
            errors, _ = validate_report.validate(write_report(directory, report), False)

        self.assertFalse(any("System-design section must contain" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
