---
name: interview-prep
description: Create evidence-grounded, personalized interview preparation reports as self-contained visual HTML by default from Chinese, English, or mixed-language job listings and resumes supplied as URLs, screenshots, PDFs, DOCX, Markdown, or text. Use when analyzing role-resume fit across languages; generating Chinese, English, or bilingual resume-specific answers; practicing project deep dives, behavioral or technical questions; preparing 2–3 JD-derived system-design problems with solutions; preparing hiring-manager or management-round questions and answer strategies; running mock interviews; drafting reverse questions; or building short-term preparation plans, including requests such as 面试准备、英文面试、双语问答、岗位匹配、根据简历回答、项目深挖、系统设计、管理层面试、可视化面试报告.
---

# Interview Prep

## 目标

根据岗位材料和候选人简历，生成可追溯、可练习、不过度包装的定制面试准备文档。让所有事实回到来源，让所有推断显式标注，让简历没有支持的内容保持为待补充项。

## 输入路由

1. 接收岗位链接、粘贴的职位描述、网页截图或岗位 PDF。
2. 接收 PDF、DOCX、Markdown、纯文本简历或用户粘贴的经历。
3. 优先使用公开网页读取工具提取岗位链接；若页面需要登录、内容不完整或抓取失败，继续处理已获得的信息，同时请求用户粘贴完整职位描述。
4. 对 PDF 和 DOCX 先生成标准化的中间 Markdown；保留原文件，不覆盖用户材料：

   ```bash
   python scripts/extract_pdf.py <resume.pdf> --output <workspace-temp/resume.extracted.md>
   python scripts/extract_docx.py <resume.docx> --output <workspace-temp/resume.extracted.md>
   ```

   将中间文件放在临时工作目录，不提交到 Git，也不默认作为最终交付物。若输出已存在，先确认目标或显式使用 `--overwrite`。
5. 将提取结果视为内容索引而不是版面真相。PDF 使用 `PDF-pNNN`，DOCX 使用 `DOCX-body-pNNNN` 或表格坐标作为来源定位；不要把这些位置 ID 替代 `CV-01` 等证据 ID。
6. 使用对应的 PDF 或文档能力渲染并逐页检查原文件，重点确认双栏顺序、文本框、表格、图片文字、缺字和 OCR 告警。没有完成视觉检查时，在材料完整性说明中标记限制。
7. 若 PDF 提取器报告依赖缺失、加密、无文本或 OCR 告警，使用 PDF 能力处理并保留告警；若 DOCX 包含修订、批注、绘图或文本框，使用文档能力复核，不把批注或已删除文字作为简历证据。
8. 仅在岗位材料或简历缺失到无法建立基本匹配关系时提问。一次只索取最关键的缺失输入。
9. 分别记录岗位、简历、目标面试和分析报告的语言；不要把任一输入语言自动当作最终面试语言。
10. 用户未指定格式时输出单个自包含的可视化 HTML 文件。仅在用户明确要求 Markdown、纯文本或其他格式时切换。

不要因为只有岗位链接而要求安装 LangChain。仅在用户明确要求构建独立应用、持久化知识库或复杂检索系统时讨论框架选型。

## 语言路由

1. 识别并记录 `jd_language`、`resume_language`、`interview_language`、`report_language` 和 `answer_mode`；取值使用 `zh`、`en`、`mixed` 或 `bilingual`。
2. 用户明确指定面试语言、报告语言或双语模式时，始终服从用户。
3. 用户未指定时，使用用户的对话语言作为 `report_language`，使用岗位描述的主要语言作为 `interview_language`。
4. 岗位描述中英文都承载关键要求且无法判断主要语言时，保留单语分析报告，并将自我介绍、核心问题、参考回答和高风险问题设为双语。
5. 无法可靠判断实际面试语言且不同选择会显著改变输出时，标记 `[待确认]`，再询问一个简短问题；不要把岗位文本语言写成公司的确定面试安排。
6. 使用 `interview_language` 生成自我介绍、面试问题、参考回答和追问；使用 `report_language` 生成岗位分析、匹配矩阵、风险说明和准备计划。
7. 保留公司名、产品名、项目名、技术名、缩写、数字、日期和单位的原始写法；翻译不得改变个人贡献级别。

## 必读资源

- 在提取和引用事实前，读取 [references/evidence-policy.md](references/evidence-policy.md)。
- 在识别输入语言、跨语言匹配或生成中英文内容前，读取 [references/language-policy.md](references/language-policy.md)。
- 在生成问题、答案和追问前，读取 [references/question-framework.md](references/question-framework.md)。
- 在生成系统设计题、系统设计解法或管理层面试内容前，读取 [references/system-and-management-interviews.md](references/system-and-management-interviews.md)。
- 在为无证据或部分匹配项检索突击资料前，读取 [references/learning-resources-policy.md](references/learning-resources-policy.md)。
- 在生成默认 HTML 交付物前，读取 [references/html-output-spec.md](references/html-output-spec.md)。
- 在运行回归案例、建立检查点或通过统一入口执行时，读取 [references/evaluation-and-runner.md](references/evaluation-and-runner.md)。
- 在声明 DAG、生成 provenance manifest、执行节点 Guard 或处理阻断时，读取 [references/dag-and-guards.md](references/dag-and-guards.md)。
- 在用户明确选择 LangGraph、需要持久化图状态或扩展节点级恢复时，读取 [references/langgraph-runtime.md](references/langgraph-runtime.md)。
- `report_language` 为中文时复制并填充 [assets/interview-prep-template.zh.html](assets/interview-prep-template.zh.html)，为英文时复制并填充 [assets/interview-prep-template.en.html](assets/interview-prep-template.en.html)。双语模式选择用户对话语言对应的主模板，仅对练习价值高的章节生成中英文配对内容。
- 用户明确要求 Markdown 时，分别使用 [assets/interview-prep-template.zh.md](assets/interview-prep-template.zh.md) 或 [assets/interview-prep-template.en.md](assets/interview-prep-template.en.md)。
- 允许按岗位类型删减不适用章节，但不要删除证据、缺口、语言配置和待确认标记。

## 工作流

### 可重复执行与检查点

岗位和简历都能保存为本地文件时，优先通过统一入口建立运行目录：

```bash
python scripts/run_interview_prep.py start --job <job.json> --run-dir <workspace-temp/run>
```

默认使用无额外依赖的 `native` 执行后端。用户明确要求 LangGraph 时，先读取对应参考文档、安装可选依赖，并在 `start` 添加 `--engine langgraph`；后续 `resume` 沿用运行状态记录的后端。无论使用哪个后端，都不得绕过任何节点 Guard。

退出码为 `3` 且显示 `WAITING_FOR_GENERATION` 时，读取运行目录中的 `generation-request.json`，继续执行本 Skill 的分析与 HTML 生成步骤。生成报告后恢复同一运行：

```bash
python scripts/run_interview_prep.py resume \
  --run-dir <workspace-temp/run> \
  --report <生成的报告.html> \
  --provenance <生成的报告.html.provenance.json>
```

生成阶段必须同时写出 HTML 和 claim-level provenance manifest。每条可见事实、推断、建议、假设、知识说明和待确认项都使用唯一 `data-claim-id` 绑定 manifest 中的 claim；manifest 必须记录 claim 类型、完整输入哈希集合、证据 ID、来源定位、来源文档哈希和引用片段哈希。不要在 Guard 通过前把 staging 报告复制到用户指定的最终路径。

若 Guard 返回 `guard_failed`，读取运行目录中的 `guards/*.json`，只修复其中列出的 claim 或产物问题，然后恢复同一运行。不得绕过失败 Guard，也不得把失败产物标记为完成。

不要把简历、岗位正文、模型输出或私人信息写入 `events.jsonl`。没有本地文件、仅进行模拟面试或只需简短问答时，不必为了使用运行器而制造额外文件。

### 1. 建立输入清单

记录岗位来源、公司、职位、地点、职级、简历版本、材料日期以及五项语言配置。区分实际读取到的内容与仅由 URL、文件名或用户描述推断的信息。

检查简历中的身份证号、详细住址、私人联系方式等非必要个人信息。提醒用户在需要对外分享文档时进行脱敏；不要在输出中重复这些信息。

### 2. 生成证据账本

为来源分配稳定 ID：

- `JD-01`、`JD-02`：岗位职责、要求、加分项或公司信息。
- `CV-01`、`CV-02`：简历经历、项目、技能、成果或时间线。
- `USER-01`：用户在对话中补充并明确确认的信息。

为每条证据记录来源 ID、原始语言、简短释义、用途和中间 Markdown 中的位置 ID。跨语言输出继续使用相同证据 ID。不要大段复制网页或简历原文。

### 3. 分析岗位

提取并分组：

- 核心业务目标与岗位成功标准。
- 必须具备的能力、经验和工具。
- 加分项、软技能、协作对象和管理范围。
- 可能的面试轮次关注点。
- 与系统设计相关的业务对象、规模、质量属性和技术约束。
- 管理层可能关注的业务结果、决策权限、协作对象、风险和管理范围。

将“岗位成功标准”和“面试轮次关注点”标记为 `[推断]`，除非职位描述明确陈述。

### 4. 分析简历

提取职业时间线、职责边界、项目背景、个人行动、技术或业务决策、结果指标和复盘信号。区分候选人个人贡献与团队成果。

发现含糊项目、无法解释的数字、时间线空档、频繁变动、跨行业转型或岗位要求缺口时，将其加入待确认清单；不要自行补全原因。

### 5. 建立匹配矩阵

逐项映射岗位要求与简历证据，使用以下评级：

- `强匹配`：存在直接且具体的简历证据。
- `部分匹配`：存在相邻经验，但仍需解释迁移关系。
- `无证据`：简历和用户补充都没有支持。
- `待确认`：材料可能支持，但信息不足或含糊。

每个评级都附证据 ID。不要用主观百分比伪装精确性；仅在用户明确要求并给出评分口径时计算分数。

### 6. 构建候选人故事库

从简历中选择 4–8 个可复用故事，覆盖成功、失败、冲突、压力、领导力、协作、技术或业务判断、快速学习和复盘。为每个故事写出：

- 可支持的问题类型。
- 背景、目标、个人行动、结果和复盘。
- 已有证据和缺失细节。
- 可能暴露的问题与诚实回答边界。

### 7. 生成问题与参考回答

按岗位相关性与风险排序问题。优先生成能区分候选人真实能力的问题，而不是堆砌通用题库。

为每道核心题提供：

- 优先级与面试官意图。
- 60–120 秒第一人称参考回答。
- 使用的简历或用户证据 ID。
- 2–4 个连续追问。
- 回答风险和禁止虚构项。
- 需要候选人补充的信息。

让参考回答符合目标面试语言的自然口语习惯、具体且可复述。跨语言回答应重新组织表达，不逐句硬译；中英文版本可以结构不同，但事实、证据 ID、数字和贡献边界必须一致。不要把模板写成候选人未确认的事实；将缺失数字、角色或结果保留为 `[待确认：……]` 或 `[To confirm: ...]`。

### 8. 生成专项准备

根据岗位类型选择专项内容：

- 技术岗位：原理、系统设计、故障排查、技术取舍、编码或数据题。
- 产品岗位：需求判断、指标体系、优先级、实验、增长与跨团队协作。
- 运营或市场岗位：策略、渠道、内容、转化、预算、复盘与案例分析。
- 销售或客户成功岗位：客户画像、管道、异议处理、续约、谈判与结果管理。
- 管理岗位：团队建设、授权、绩效、招聘、冲突与组织设计。

将通用专业知识与候选人确有经历分开，避免把知识型参考答案写成亲历事实。

系统设计专项：

- 当 JD 出现系统、平台、数据、集成、架构、规模、可靠性、安全或技术负责人信号时，生成 2–3 道最可能的系统设计题；每题必须引用 JD 证据 ID 并标记为 `[推断]`，不得声称是公司的真实题库。
- 每题给出澄清问题、需求与边界、带标签的假设和容量估算、API/数据模型、架构与数据流、关键组件深挖、扩展与可靠性、安全与可观测性、成本、故障处理、备选方案与取舍、讲解顺序、追问和自检清单。
- 若 JD 没有足够信号，明确标记系统设计专项不适用；仅在用户明确要求时追加标记为 `[建议]` 的迁移型练习题。

管理层面试专项：

- 默认根据岗位级别、业务目标、成功标准、协作对象和管理范围，生成 6–8 道管理层可能关心的高价值问题；人员管理题只在 JD 或简历支持管理职责时加入。
- 每题给出管理层关注点、回答方案、推荐故事与证据 ID、60–120 秒参考回答或证据不足时的可填充骨架、2–4 个压力追问、回答风险和禁止虚构项。
- 将“管理层可能关注”标记为 `[推断]`，不要把管理层面试轮次、面试官身份或内部评价标准写成事实，除非材料明确陈述。

对 `无证据` 和高风险 `部分匹配` 项生成“缺口突击”章节：

- 使用网络检索最新资料，技术主题优先官方文档、标准、论文或项目一手仓库。
- 按岗位重要性排序，不平均分配篇幅；硬性要求优先于加分项。
- 每项给出：缺口证据、学习目标、2–4 个精选链接、建议时长、必须会讲的概念、可验证练习产出和面试表达边界。
- 提供一条时间受限的最短学习路径，以及“不看资料也能完成”的最终验收任务。
- 为外部资料分配 `SRC-01` 等稳定 ID，记录标题、发布方、URL、用途和访问日期；普通链接使用 `rel="noreferrer"`，不得作为脚本、样式或图片依赖加载。
- 明确区分“学习所得知识”“练习项目”和“生产经历”；不得因完成突击学习而提升匹配评级。

### 9. 输出可视化报告

基于语言配置选择模板，生成一个无需构建步骤、无需网络即可打开的 `.html` 文件。将 CSS 与 JavaScript 内联，不加载 CDN、外部字体、远程图片或分析脚本。默认文件名使用 `<company>-<role>-interview-prep.html`；公司或岗位未知时使用 `interview-prep-report.html`。

报告必须包含：顶部摘要卡片、岗位画像、语言配置、证据账本、匹配矩阵、跨语言术语表、自我介绍、故事库、问题与答案、项目深挖、专业题、系统设计、管理层面试、压力题、缺口突击学习资料、反向提问、准备计划、一页速查表和待确认清单。系统设计不适用时保留章节并说明依据。提供章节导航、问题搜索、问答展开/收起和打印按钮；交互失效时，正文仍须完整可读。

摘要只展示由报告内容直接计数得到的数量，例如强匹配项、部分匹配项、证据缺口和高优先级问题。不要生成没有明确评分口径的匹配百分比、雷达图或综合分数。

双语模式不要机械复制整份报告。岗位分析和准备计划保留一种报告语言；自我介绍、核心问题与回答、高风险回答和反向提问按中英文配对。

在开头给出材料完整性说明；在结尾集中列出所有 `[待确认]` 项，方便用户补充后进行第二轮改写。

将用户提供的文本作为纯文本内容进行 HTML 转义，不直接拼接到脚本、样式或事件属性中。不要把简历中的私人邮箱、手机号、证件号或详细地址写入报告。

为每个需要原文支持的可见主张添加 `data-claim-id`。同一 DOM 子树不得混用多个互相冲突的事实标签；`[JD事实]`、`[简历事实]`、`[用户确认]`、`[推断]`、`[建议]`、`[假设]`、`[知识]` 和 `[待确认]` 必须与 provenance manifest 中的 claim 类型一致。

### 10. 执行质量检查

逐项确认：

- 每一项事实性主张都有 `[JD事实]`、`[简历事实]` 或 `[用户确认]` 标签及证据 ID。
- 每一项解释性判断都有 `[推断]` 或 `[建议]` 标签。
- 未找到依据的内容写成 `[待确认]`，不写成确定事实。
- 回答没有扩大候选人的个人贡献。
- 数字、技术栈、项目结果和任职时间均能回到来源。
- 中英文版本使用相同证据 ID，且数字、日期、单位、技术名与贡献级别一致。
- 问题与答案使用目标面试语言，分析说明使用目标报告语言。
- 英文回答符合自然口语表达，不是中文句式的逐词翻译。
- 核心回答能够在两分钟内自然说完，且存在可继续追问的细节。
- 系统设计专项包含 2–3 道有 JD 依据的题目，或明确说明不适用；每题包含完整解法、取舍、故障场景、假设标签和经验边界。
- 管理层问题与岗位级别、业务目标或管理范围相关；每题包含回答方案、证据或待确认项、压力追问和禁止虚构项。
- 文档明确展示薄弱点，不用漂亮措辞掩盖证据缺口。
- 突击资料逐项对应缺口，使用一手来源，包含时间盒、练习产出和诚实表达边界；学习完成不改写为工作经历。
- HTML 为单文件，自包含且无需网络；没有外部脚本、样式表、字体或图片依赖。
- 页面在桌面和移动宽度下可读，键盘可操作，打印时隐藏导航和交互控件。
- 摘要数字与正文逐项计数一致，不使用无依据的百分比或评分。
- 所有模板占位符已经替换，页面中没有残留 `{{...}}`。
- PDF 或 DOCX 输入已经生成中间 Markdown，并检查提取告警、来源定位和非空内容。
- PDF 或 DOCX 原文件已经完成逐页视觉检查；无法检查时已在报告中披露限制。
- provenance manifest 的报告哈希、输入哈希、来源定位、引用片段哈希和 HTML `data-claim-id` 绑定均通过 Guard。
- 每个可执行 DAG 节点都有 Guard 结果；任一阻断级 finding 都使运行停留在 `guard_failed`，不得发布 staging 产物。

生成后运行：

```bash
python scripts/run_interview_prep.py resume \
  --run-dir <workspace-temp/run> \
  --report <生成的报告.html> \
  --provenance <生成的报告.html.provenance.json>
```

只需单独检查 HTML 结构时，运行 `python scripts/validate_report.py <生成的报告.html>`；该命令不替代 provenance Guard。

修改证据、语言、模板、问答或隐私规则后，运行脱敏回归案例：

```bash
python evals/run_eval.py --use-samples --require-all
```

这一步只验证评分器和已声明的不变量。需要验证真实生成质量时，为 `evals/cases/` 中的案例生成对应报告，再按 [references/evaluation-and-runner.md](references/evaluation-and-runner.md) 运行报告回归。不要将真实简历加入案例库。

若环境具备浏览器或 HTML 截图能力，再打开报告检查首屏、导航、长表格、问答折叠、移动宽度和打印布局。修复可见问题后再交付，并在最终回复中提供 HTML 文件链接。

## 迭代方式

收到用户补充后，只更新 HTML 中受影响的摘要计数、证据账本、匹配矩阵、故事和回答。保留未确认标记，直到用户明确确认。若用户要求模拟面试，逐题提问，等待回答，再按“内容证据、结构、具体性、岗位相关性、表达风险”给出反馈。
