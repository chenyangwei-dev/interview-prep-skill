#!/usr/bin/env python3
"""Run deterministic regression cases against reports or bundled safe samples."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from eval_report import evaluate_path, evaluate_text, load_case, write_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "cases")
    parser.add_argument("--reports", type=Path, help="Directory containing <case_id>.html/.md/.txt")
    parser.add_argument("--use-samples", action="store_true", help="Evaluate each case's synthetic sample_output")
    parser.add_argument("--require-all", action="store_true", help="Fail when a report for a case is missing")
    parser.add_argument("--output", type=Path, default=ROOT / "work" / "eval-results.json")
    return parser.parse_args()


def find_report(directory: Path, case_id: str) -> Path | None:
    for suffix in (".html", ".md", ".txt"):
        candidate = directory / f"{case_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    args = parse_args()
    case_paths = sorted(args.cases.glob("*.json"))
    if not case_paths:
        print(f"ERROR: No cases found in {args.cases}", file=sys.stderr)
        return 2
    if not args.use_samples and args.reports is None:
        print("ERROR: Provide --reports or --use-samples.", file=sys.stderr)
        return 2

    results = []
    skipped = []
    for case_path in case_paths:
        case = load_case(case_path)
        if args.use_samples:
            result = evaluate_text(str(case.get("sample_output", "")), case)
            result["report"] = "<bundled-safe-sample>"
            results.append(result)
            continue

        report = find_report(args.reports, case["case_id"])
        if report is None:
            skipped.append(case["case_id"])
            continue
        results.append(evaluate_path(report, case))

    summary = {
        "schema_version": 1,
        "total_cases": len(case_paths),
        "evaluated": len(results),
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "skipped": skipped,
        "results": results,
    }
    write_json(args.output, summary)
    print(json.dumps({key: summary[key] for key in ("total_cases", "evaluated", "passed", "failed", "skipped")}, ensure_ascii=False))
    if args.require_all and skipped:
        return 1
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
