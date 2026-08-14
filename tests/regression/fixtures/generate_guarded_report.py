#!/usr/bin/env python3
"""Generate a minimal structurally valid report and exact provenance for tests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SECTION_IDS = (
    "overview",
    "match",
    "risks",
    "introductions",
    "stories",
    "questions",
    "deep-dive",
    "system-design",
    "management-interview",
    "reverse",
    "plan",
    "cheat-sheet",
    "evidence",
    "confirmations",
    "integrity",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def exact_excerpt(text: str) -> tuple[str, int, int]:
    excerpt = text.split()[0] if text.split() else text
    start = text.index(excerpt)
    return excerpt, start, start + len(excerpt)


def build_report(jd_claim: str, resume_claim: str) -> str:
    sections: list[str] = []
    for section_id in SECTION_IDS:
        content = f"<h2>{section_id}</h2>"
        if section_id == "overview":
            content += (
                '<p>合成测试内容</p>'
                f'<p data-claim-id="CLM-001">[JD事实｜JD-01] {jd_claim}</p>'
                f'<p data-claim-id="CLM-002">[简历事实｜CV-01] {resume_claim}</p>'
            )
        if section_id == "match":
            content += '<div class="table-wrap"><table><tr><td>匹配</td></tr></table></div>'
        if section_id == "questions":
            content += '<input id="question-search">'
        if section_id == "system-design":
            content += "系统设计" + "".join(
                '<details data-kind="system-design"><summary>题目</summary><p>方案</p></details>'
                for _ in range(2)
            )
        if section_id == "management-interview":
            content += "管理层" + "".join(
                '<details data-kind="management-interview"><summary>问题</summary><p>方案</p></details>'
                for _ in range(6)
            )
        sections.append(f'<section id="{section_id}">{content}</section>')
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Test</title>'
        '<style>@media print{nav{display:none}} @media (max-width:600px){main{width:100%}}</style>'
        '</head><body><header><div class="summary-grid">摘要</div></header><nav>导航</nav><main>'
        '<button id="expand-all">展开</button><button id="collapse-all">收起</button>'
        '<button id="print-report">打印</button>'
        + "".join(sections)
        + "</main><script>document.querySelectorAll('button').forEach(function(button){"
        "button.addEventListener('click',function(){});});</script></body></html>"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()

    request = json.loads(args.request.read_text(encoding="utf-8"))
    inputs = request["inputs"]
    jd_path = Path(inputs["jd"]["normalized_path"])
    resume_path = Path(inputs["resume"]["normalized_path"])
    jd_text = jd_path.read_text(encoding="utf-8")
    resume_text = resume_path.read_text(encoding="utf-8")
    jd_claim, jd_start, jd_end = exact_excerpt(jd_text)
    resume_claim, resume_start, resume_end = exact_excerpt(resume_text)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(jd_claim, resume_claim), encoding="utf-8")
    claims = []
    for claim_id, evidence_id, source, source_path, source_text, claim_text, start, end in (
        ("CLM-001", "JD-01", "jd", jd_path, jd_text, jd_claim, jd_start, jd_end),
        ("CLM-002", "CV-01", "resume", resume_path, resume_text, resume_claim, resume_start, resume_end),
    ):
        claims.append(
            {
                "claim_id": claim_id,
                "claim_type": "source_fact",
                "text": claim_text,
                "basis_claim_ids": [],
                "evidence_refs": [
                    {
                        "evidence_id": evidence_id,
                        "source": source,
                        "source_document_sha256": sha256_path(source_path),
                        "locator": "document",
                        "span_start": start,
                        "span_end": end,
                        "span_sha256": sha256_bytes(source_text[start:end].encode("utf-8")),
                    }
                ],
            }
        )
    manifest = {
        "schema_version": 1,
        "report_sha256": sha256_path(args.output),
        "input_sha256": {
            name: metadata["normalized_sha256"] for name, metadata in inputs.items()
        },
        "claims": claims,
    }
    args.provenance.parent.mkdir(parents=True, exist_ok=True)
    args.provenance.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
