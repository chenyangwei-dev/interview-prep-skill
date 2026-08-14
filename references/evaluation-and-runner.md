# 评测与统一运行入口

## 目标

[事实｜依据：当前 runner] 统一入口用 DAG 状态记录准备、生成、HTML 校验、可选评测和最终发布，并在每个 runtime 节点写入 Guard 结果。

[边界｜依据：当前实现] 当前 runtime 仍把内容生成作为一个粗粒度节点；完整内容 DAG 已声明，但尚未逐节点执行或缓存。详见 [dag-and-guards.md](dag-and-guards.md)。

## 阶段 1：回归评测

[事实｜依据：`evals/run_eval.py`] `evals/cases/` 使用合成且脱敏的约束，能够检查证据 ID、禁止措辞、数字一致性、双语一致性和隐私测试值。

运行内置安全样例：

```bash
python evals/run_eval.py --use-samples --require-all
```

评测实际报告时，使用案例 ID 作为文件名：

```text
work/eval-reports/
├── backend-engineer-zh.html
├── product-manager-en.html
└── bilingual-transition.html
```

```bash
python evals/run_eval.py \
  --reports work/eval-reports \
  --require-all \
  --output work/eval-results.json
```

[边界｜依据：规则评分范围] 规则评测只能证明已声明约束没有回归，不能证明整份报告事实正确。新增真实失败模式时，先脱敏，再缩减成合成案例。

## 阶段 2：任务 JSON

[事实｜依据：`scripts/run_interview_prep.py`] 任务文件使用以下结构；`evaluation` 可省略：

```json
{
  "schema_version": 1,
  "jd": {"path": "inputs/jd.md"},
  "resume": {"path": "inputs/resume.pdf"},
  "user": {"path": "inputs/user-confirmed.md"},
  "sources": [
    {
      "id": "SRC-01",
      "path": "inputs/backend-standard.md",
      "url": "https://example.com/backend-standard",
      "accessed_at": "2026-08-14"
    }
  ],
  "languages": {
    "jd_language": "zh",
    "resume_language": "zh",
    "interview_language": "zh",
    "report_language": "zh",
    "answer_mode": "single"
  },
  "output": {"report_path": "outputs/company-role-interview-prep.html"},
  "evaluation": {"case_path": "../evals/cases/backend-engineer-zh.json"}
}
```

开始运行：

```bash
python scripts/run_interview_prep.py start \
  --job work/job.json \
  --run-dir work/runs/example
```

[事实｜依据：等待协议] 未配置生成器时，命令以退出码 `3` 返回 `WAITING_FOR_GENERATION`。读取 `generation-request.json`，生成其指定的 staging HTML 与 provenance manifest，再恢复同一运行：

```bash
python scripts/run_interview_prep.py resume \
  --run-dir work/runs/example \
  --report outputs/company-role-interview-prep.html \
  --provenance outputs/company-role-interview-prep.html.provenance.json
```

[事实｜依据：sidecar 约定] 若省略 `--provenance`，runner 只会自动查找 `<report>.provenance.json`；不存在时阻止恢复。

## 外部生成器适配

[事实｜依据：generator adapter] 独立生成器可使用 `{request}`、`{output}`、`{provenance}` 和 `{run_dir}` 占位符：

```bash
python scripts/run_interview_prep.py start \
  --job work/job.json \
  --generator-command 'my-generator --request {request} --output {output} --provenance {provenance}'
```

[事实｜依据：subprocess 调用] 命令按参数数组执行，不启用 shell。生成器应直接写 `generation-request.json` 中的 staging 路径，不能提前写最终输出路径。

## 查看 DAG 与运行状态

```bash
python scripts/run_interview_prep.py plan
python scripts/run_interview_prep.py plan --runtime
python scripts/run_interview_prep.py status --run-dir work/runs/example
```

[事实｜依据：`plan`] 默认命令显示完整内容 DAG；`--runtime` 显示当前实际执行的粗粒度 DAG。

## 状态、产物和隐私

[事实｜依据：runner 写入路径] 每次运行最多生成：

- `state.json`：schema v2 状态、节点状态、输入/产物哈希和失败原因。
- `events.jsonl`：节点开始/结束、耗时、字节数、返回码和错误类型。
- `generation-request.json`：标准化输入路径、输入哈希、语言配置、staging 路径和最终目标。
- `guards/*.json`：准备、报告、结构校验和评测 Guard 结果。
- `staging/`：尚未通过 Guard 的候选产物。
- `artifacts/report.html` 与 `artifacts/report.provenance.json`：通过 claim-level Guard、但尚未最终发布的内部产物。
- `validation.json`：HTML 结构校验结果。
- `evaluation.json`：配置 `evaluation.case_path` 时的确定性评分结果。

[事实｜依据：事件写入字段] `events.jsonl` 不记录 JD、简历、报告正文或外部生成器 stdout/stderr；它只记录路径、哈希、状态与诊断元数据。

[边界｜依据：本地文件设计] 正文仍存在于输入文件、标准化副本、staging 和最终报告中；这些文件的访问控制与清理策略由运行环境负责。

## Guard 失败处理

[事实｜依据：阻断逻辑] 任一阻断级 finding 会把节点和运行标记为 `guard_failed`，且 staging 报告不会被提升到最终路径。

[事实｜依据：finalize 门禁] 只有 `prepare`、`generate_report`、`validate_report` 和 `evaluate_report` 均为成功或显式跳过状态，且内部报告与 provenance hash 未改变时，`finalize` 才发布最终 HTML 和相邻 sidecar manifest。

[建议｜依据：可恢复状态] 修复 `guards/*.json` 指向的 claim、manifest 或输入问题后，使用同一 `--run-dir` 再次 `resume`，以保留运行历史。

[禁止｜依据：发布门禁] 不要手工复制失败的 staging 产物到最终路径，也不要只运行 `validate_report.py` 来替代 provenance Guard。

## 扩展约束

[事实｜依据：适配边界] `generation-request.json` 是模型 SDK、服务或可观测平台的适配边界；供应商调用逻辑不写入 `SKILL.md`。

[边界｜依据：当前语义验证] 原文包含关系能够由本地确定性 Guard 验证；释义、归纳和跨语言改写在未配置独立语义检查器时会被阻断，而不是自动判为有依据。
