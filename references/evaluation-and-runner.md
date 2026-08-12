# 评测与统一运行入口

## 目标

用确定性检查捕获证据、跨语言一致性、贡献边界和隐私回归；用统一入口记录运行状态，而不把简历或岗位正文写入日志。

## 阶段 1：回归评测

评测案例位于 `evals/cases/`，均使用合成且脱敏的输入约束。每个案例声明：

- 允许和必须出现的证据 ID。
- 必须和禁止出现的措辞。
- 必须保持的数字及其证据 ID。
- 双语内容中需要重复出现的数字或证据。
- 只用于验证隐私检测、绝不能出现在报告中的测试值。

运行内置安全样例，验证评分器自身：

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

然后运行：

```bash
python evals/run_eval.py \
  --reports work/eval-reports \
  --require-all \
  --output work/eval-results.json
```

规则评分只能证明声明过的约束没有回归，不能证明整份报告事实正确。新增真实失败模式时，先脱敏，再将其缩减成新案例。

## 阶段 2：统一运行入口

创建不含秘密的任务 JSON：

```json
{
  "schema_version": 1,
  "jd": {"path": "inputs/jd.md"},
  "resume": {"path": "inputs/resume.pdf"},
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

`evaluation` 是可选项。配置后，报告通过 HTML 结构校验后还会执行指定案例的确定性评分；结果写入 `evaluation.json`，失败状态为 `evaluation_failed`。

开始运行：

```bash
python scripts/run_interview_prep.py start \
  --job work/job.json \
  --run-dir work/runs/example
```

没有生成器时，命令以退出码 `3` 返回 `WAITING_FOR_GENERATION`。读取运行目录里的 `generation-request.json`，按 `SKILL.md` 生成目标报告，再恢复：

```bash
python scripts/run_interview_prep.py resume \
  --run-dir work/runs/example \
  --report outputs/company-role-interview-prep.html
```

独立应用可传入无 shell 执行的命令模板：

```bash
python scripts/run_interview_prep.py start \
  --job work/job.json \
  --generator-command 'my-generator --request {request} --output {output}'
```

可用占位符为 `{request}`、`{output}` 和 `{run_dir}`。生成命令通过参数数组执行，不启用 shell。

## 状态与隐私

每次运行生成：

- `state.json`：当前状态、完成步骤、版本、报告路径和哈希。
- `events.jsonl`：步骤开始/结束、耗时、字节数、返回码和错误类型。
- `generation-request.json`：标准化输入路径、语言配置和输出目标。
- `validation.json`：HTML 校验结果。
- `evaluation.json`：配置 `evaluation.case_path` 时的确定性评分结果。

事件日志不记录 JD、简历或报告正文，也不记录外部生成器的 stdout/stderr。正文只存在于用户指定的输入、中间文件和最终报告中。

## 扩展约束

在接入模型 SDK、服务或 Langfuse 时，保持 `generation-request.json` 为适配边界，不要把供应商调用逻辑写入 `SKILL.md`。外部适配器必须生成目标 HTML，并保留运行入口的状态、校验和隐私规则。

当实际生成回归耗时增长时，按 [regression-testing-plan.md](regression-testing-plan.md) 分层执行 PR 冒烟、Nightly 全量和发布前深度评估。
