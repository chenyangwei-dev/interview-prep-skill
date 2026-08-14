# LangGraph 可选执行后端

## 适用范围

[事实｜依据：`scripts/langgraph_runtime.py`] `--engine langgraph` 将现有五个 runtime 节点映射为 LangGraph `StateGraph`：`prepare → generate_report → validate_report → evaluate_report → finalize`。

[边界｜依据：执行器职责] LangGraph 只负责路由和 checkpoint。`scripts/guards.py`、provenance manifest、staging、artifact promotion 和最终发布门禁仍是事实可信边界；不要用 checkpoint 状态替代 Guard 结果。

[建议｜依据：依赖隔离] 普通单次运行继续使用默认 `native` 后端。需要持久化图状态、后续节点拆分或人工审核扩展时，再安装可选依赖并选择 LangGraph：

```bash
python -m pip install -r requirements-langgraph.txt
python scripts/run_interview_prep.py start \
  --job work/job.json \
  --run-dir work/runs/example \
  --engine langgraph
```

[事实｜依据：resume 参数路由] 使用 LangGraph 创建的运行会在 `state.json` 中记录 `orchestrator=langgraph`；后续 `resume` 默认沿用该后端，也可以显式传入 `--engine langgraph`。

## Checkpoint 与隐私

[事实｜依据：`ExecutionState`] `langgraph-checkpoints.sqlite` 只保存 `run_id`、运行目录、操作类型、返回码、暂停状态和最后节点，不保存 JD、简历、报告正文或 claim 内容。

[事实｜依据：现有 runner] `state.json` 继续保存 artifact 路径、哈希和 Guard 状态，并作为 CLI 恢复与发布门禁的权威状态。SQLite checkpoint 是编排轨迹，不是事实证据库。

[建议｜依据：副作用重放风险] 节点写入必须继续遵循 staging → Guard → promote，并保持原子复制和可重复执行。不要在 LangGraph 节点通过 Guard 前直接写最终报告。

## 失败与等待语义

[事实｜依据：conditional edges] 任一节点返回非零退出码时，LangGraph 结束当前调用，不会调度下游节点。退出码 `3` 保持现有 `WAITING_FOR_GENERATION` 协议；Guard、验证或评测失败返回非零并保留失败节点状态。

[事实｜依据：惰性导入] 未安装可选依赖时，默认 `native` 后端仍可运行；显式选择 `langgraph` 会返回可操作的安装错误。

## 扩展约束

[建议｜依据：可信边界复用] 后续拆分 evidence、match、story、system-design 和 management 节点时，为每个节点复用同一套 claim schema、原文 span/hash 校验和 semantic checker，不要让 LangGraph 节点直接把模型输出标记为成功。

[建议｜依据：最小化持久化] 新增 State 字段前先判断是否含个人信息或模型正文。此类数据继续只通过本地 artifact 路径引用，不写入 LangGraph State。
