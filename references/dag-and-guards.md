# DAG 与原文溯源 Guard

## 目标

[事实｜依据：runner 实现] 统一入口使用 `run → stage → guard → promote`，未经 Guard 通过的产物不能进入最终输出或下游步骤。

[事实｜依据：`scripts/dag.py`] 代码声明了完整内容 DAG 和当前可执行的 runtime DAG。完整内容 DAG 用于约束未来的节点级拆分；runtime DAG 当前执行 `prepare → generate_report → validate_report → evaluate_report（可选）→ finalize`。

[事实｜依据：`scripts/langgraph_runtime.py`] runtime DAG 可以由默认 `native` 后端或可选 LangGraph `StateGraph` 后端执行；两者调用相同节点函数和 Guard。LangGraph 的安装、checkpoint 与隐私约束见 [langgraph-runtime.md](langgraph-runtime.md)。

## 强制 Guard

[建议｜依据：引用型幻觉风险] 不要把“存在 Evidence ID”当作原文支持。生成报告必须同时输出 provenance manifest，并让每个可见主张使用 `data-claim-id` 绑定 manifest claim。

[事实｜依据：`scripts/guards.py`] 当前 Guard 包含：

- Schema：检查 request、manifest、claim 和 Evidence ID 的结构。
- Provenance：检查标准化源文件 hash、block locator、字符 span 和 span hash。
- Rules：检查数字冲突、贡献动词升级、来源类型和标签。
- Semantic grounding：原文完全包含 claim 时确定性通过；其余内容需要独立 semantic checker 返回 `supported`，否则按 `ambiguous` 阻断。

[判断｜依据：语义检查边界] 独立模型检查器仍可能误判，所以不能绕过确定性的 source span、hash、数字和贡献规则。

## Claim 类型

| 类型 | 必需字段 | 规则 |
|---|---|---|
| `source_fact` | `evidence_refs` | [建议] 必须有精确原文 span；可见文本使用事实标签。 |
| `derived_fact` | `basis_claim_ids`、`formula`、`inputs`、`value` | [建议] 必须显示 `[计算事实]` 并通过受限公式重放。 |
| `inference` | `basis_claim_ids` | [建议] 必须显示 `[推断]`；Guard 会追溯 basis 到已验证原文，并要求 semantic checker 返回 `supported`。 |
| `recommendation` | `basis_claim_ids` 或 `policy_refs` | [建议] 必须显示 `[建议]`，不能声称已实际实施。 |
| `assumption` | `scope` | [建议] 必须显示 `[假设]`，不能进入事实摘要。 |
| `knowledge` | `evidence_refs` | [建议] 可变技术、法规、安全或产品事实必须有 SRC 原文。 |
| `unknown` | `missing_fields` | [建议] 只能显示 `[待确认]`，不得自动补全。 |

## Provenance manifest

[建议｜依据：`guard_report()`] manifest 使用 schema version 1：

```json
{
  "schema_version": 1,
  "report_sha256": "<report sha256>",
  "input_sha256": {
    "jd": "<normalized JD sha256>",
    "resume": "<normalized resume sha256>"
  },
  "claims": [
    {
      "claim_id": "CLM-001",
      "claim_type": "source_fact",
      "text": "岗位要求后端系统设计能力。",
      "evidence_refs": [
        {
          "evidence_id": "JD-01",
          "source": "jd",
          "source_document_sha256": "<normalized JD sha256>",
          "locator": "document",
          "span_start": 0,
          "span_end": 14,
          "span_sha256": "<exact span sha256>"
        }
      ],
      "basis_claim_ids": []
    }
  ]
}
```

[建议｜依据：HTML claim binding] 报告中对应内容使用：

```html
<p data-claim-id="CLM-001"><span>[JD事实｜JD-01]</span> 岗位要求后端系统设计能力。</p>
```

[事实｜依据：输入集合 Guard] manifest 的 `input_sha256` 必须与 generation request 的完整标准化输入集合完全一致；缺少未被引用的输入也会阻断，避免报告悄悄忽略某份材料。

## 派生事实重放

[事实｜依据：`replay_derived_fact()`] `formula` 不是可执行表达式，而是受限对象；当前允许 `count`、`sum`、`difference`、`product`、`ratio` 和 `percentage`，可选 `precision` 为 `0–12`。

```json
{
  "claim_id": "CLM-summary-001",
  "claim_type": "derived_fact",
  "text": "强匹配项共 3 项。",
  "basis_claim_ids": ["CLM-match-001", "CLM-match-002", "CLM-match-003"],
  "formula": {"operation": "count"},
  "inputs": ["CLM-match-001", "CLM-match-002", "CLM-match-003"],
  "value": 3
}
```

[事实｜依据：派生 Guard] 重算结果与 `value` 不一致、公式不受支持或渲染文本不包含 `value` 时阻断；runner 不执行 manifest 提供的任意代码。

## Semantic checker 适配

[事实｜依据：`run_semantic_checker()`] 可通过 `--semantic-guard-command` 或 `job.guards.semantic_command` 配置外部检查器；命令模板支持 `{request}`、`{output}` 和 `{run_dir}`。

[建议｜依据：最小暴露] 检查器只接收单条 claim 和它引用的 spans，不应接收整份生成报告，也不得自行补充外部事实。

[事实｜依据：输出校验] checker 输出必须是：

```json
{
  "schema_version": 1,
  "claims": [
    {"claim_id": "CLM-001", "status": "supported"}
  ]
}
```

[事实｜依据：允许值] `status` 只能为 `supported`、`partially_supported`、`unsupported` 或 `ambiguous`；只有 `supported` 可以晋级。释义型来源事实、外部知识事实和由原文导出的 inference 都遵循这一规则。

## 失败处理

[事实｜依据：runner 状态] 空 claim 集合、缺失 manifest、输入 hash 集合不一致、source hash/span 错误、未知 Evidence ID、数字冲突、贡献升级、标签来源不匹配或语义未支持时，节点进入 `guard_failed`。

[建议｜依据：审计完整性] Guard 不直接改写内容。回到原生成步骤缩小主张、补充真实来源，或降级为推断/建议/待确认，然后重新生成新的 artifact hash。

[事实｜依据：日志实现] `events.jsonl` 和 Guard 结果只记录 claim ID、finding code、状态、hash、耗时和计数，不记录原文或完整生成内容。

## 当前边界

[事实｜依据：runtime DAG] 当前 runner 已实现 `prepare → generate_report → validate_report → evaluate_report（可选）→ finalize` 的 Guard；细粒度 evidence、analysis、match、story、system-design 和 management 节点已经声明，但尚未替换整体 `generate_report` 适配器。

[判断｜依据：实现阶段] 这意味着当前能阻止未经溯源的最终报告晋级，但还不能单独恢复或缓存某一个内容章节。后续拆分时复用同一 Claim/Guard 契约。
