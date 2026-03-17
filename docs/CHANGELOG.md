# CHANGELOG / 项目阶段记录

> 本文件用于记录 Resume Analysis Tool 在不同阶段的**架构演进、主要改动**以及**下一步计划**，方便后续回顾与协作。

## 2026-03-17：缓存与投递 Tracker 增强（V1 上线准备）

- **OCR 与分析缓存**
  - OCR 层增加文件级缓存：`extract_text_with_flash` 以文件内容哈希为 key，将 Flash 多模态识别出的纯文本缓存在 `test_outputs/cache_ocr/` 下；同一 JD/简历文件（内容不变）在 CLI 与 Streamlit 中复用缓存文本，避免重复消耗 OCR token。
  - 分析层 run 级缓存：以 `jd_text + "\n====RESUME====\n" + resume_text` 的 sha256 为 key，将结构化 JD/简历、Tavily 结果、匹配结果、大牛辩论与合议结论统一缓存在 `test_outputs/cache/<hash>.json` 中；CLI 和 Streamlit 入口均可在 `FORCE_REANALYZE=false` 时复用该缓存，跳过 Tavily/匹配/辩论。
  - `main.py` 终端输出增强：在每个阶段打印输入概要（文件路径、原文预览、结构化字段）、调用的模型/API、缓存命中情况与阶段完成提示，使 CLI 日志更接近结构化运行日志。

- **Streamlit Web 前端与运行选项**
  - `app.py` 顶层拆分为两个平行模块：`JD 与简历分析` 与 `投递记录 (Tracker)`；Tracker 模块独立于分析流程，可单独使用。
  - 在分析 Tab 中新增「运行选项」：`保存到数据库（SAVE_TO_DB）` 与 `强制重新分析（FORCE_REANALYZE）` 勾选框，默认值来自 `.env`，前端可按次 override；本次分析是否入库不再仅由后端 env 决定。
  - Web 侧在分析完成后同样写入 run 级缓存，与 CLI 共享 `test_outputs/cache` 目录。

- **投递 Tracker：applications 表与可编辑表格**
  - `applications` 表作为投递 Tracker 的唯一数据源：analysis_id + 公司、职位、JD 摘要/链接、地点、薪资范围、平台、回复/面试/Offer 状态等字段（详见 `docs/tracker_implementation.md`）。
  - 分析结束后：CLI 与 Streamlit 默认自动调用 `create_from_analysis(analysis_id)`，基于当次 `Analysis` + `Job` 预填公司/职位/地点/JD 摘要，并写入 `applications` 表；用户也可通过 Web 端「将本次分析记为一次投递」或「新增投递（使用本次分析结果预填）」手动创建记录。
  - Streamlit Tracker Tab 使用 `st.data_editor` 直接展示并编辑 `applications` 列表：用户可在表格中修改公司/职位/地点/薪资/平台/回复与面试状态/面试轮次/Offer 文本；点击「保存表格中所有修改」后，逐行 diff 并调用 `update_application` 同步更新数据库。
  - Tracker Tab 上方展示投递统计（投递数/回复率/面试率/Offer 数）与「生成策略建议」按钮，调用 `run_strategy_summary` 生成策略文案。

- **数据库去重与初始化入口**
  - `save_analysis_run` 中对 Job/Resume 增加去重策略：相同公司/岗位/地点且 JD 原文完全一致时复用已有 Job 记录；相同 user_id 且简历原文完全一致时复用已有 Resume 记录，避免大量重复行；Analysis 仍为每次分析新建一条，指向可能复用的 Job/Resume。
  - 数据库初始化入口下沉到 `src/db/__init__.py`：首次导入 `src.db` 包时调用 `init_database(drop_existing=False)`，自动创建缺失表（含 applications），CLI 与 Streamlit 共用，不再在 `app.py` 中单独维护初始化逻辑。

- **文档与清理**
  - 新增并完善 `docs/tracker_implementation.md`，系统性说明投递 Tracker 的表结构、数据流与前后端协作方式。
  - 清理中间规划文档：删除 `docs/plan_frontend_and_tracker.md` 与 `docs/web_ui_plan.md`，保留最终实现文档与 `report_to_db_mapping.md`、`tavily_and_external_sources.md`、`debate_personas_intro.md` 作为架构与行为说明。

---


---

## 2026-03-14：第一阶段收尾与上线准备

- **入口统一**
  - `example_data/` 加入 `.gitignore`，JD/简历**仅从 input 目录**读取；未在 input 中找到文件时直接报错提示。
- **命令行入口更名与步骤打印**
  - `test_script.py` 更名为 **`main.py`**，作为 CLI 主入口。
  - 终端每步打印清晰步骤（1/8～8/8），并标明所用 API/模型：如「调用: Gemini API（gemini-3-flash-preview）— Flash 多模态 OCR」「调用: Tavily API（已配置密钥: 是/否）」「大牛辩论 → 角色: 王川」等，便于排查与演示。
- **前端侧栏与路径**
  - 左侧由「设置」改为**「开始分析步骤」**：1. 准备文件 2. 解析 3. 选择大牛 4. 开始分析。
  - input 目录展示改为泛用描述：`<项目根目录>/input`，不再暴露用户绝对路径。
- **文档**
  - README：项目结构更新为 input + main.py；快速开始改为「将文件放入 input 后运行 `python main.py`」；Web 启动说明与步骤指引一致。
  - CHANGELOG：补充本阶段收尾条目；报告体系与 input 说明改为 main.py 与唯一入口表述。

---

## 2026-03-14：Backlog 落地（Tavily + 投递 Tracker + 策略）

- **Tavily 优化（阶段 0）**
  - 查询构造：在 `tavily_search.py` 中为各类型查询增加「招聘」「岗位要求」「面试」「面经」等约束词，提高与求职/招聘的相关性。
  - 结果过滤：对摘要做关键词过滤（`TAVILY_FILTER_RESULTS`，默认 true），不含招聘/求职相关关键词时替换为提示文案。
  - 文档：`docs/tavily_and_external_sources.md` 记录优化方式与脉脉/猎聘/Boss 等外部数据源调研结论（当前无开放 API，以 Tavily 优化为主）。
- **数据库与分析持久化（阶段 1）**
  - `analyses` 表新增 `debate_rounds_json`、`tavily_insights_json`、`final_competitiveness_json`；`save_analysis_run` 支持传入并写入上述字段；main.py 与 app.py 分析完成后均传入辩论与 Tavily 结果。
  - 新增 `applications` 表（投递记录）：analysis_id、公司、职位、平台、流程状态等；与 Analysis 一对多关联。
  - 分析默认入库：`SAVE_TO_DB` 默认改为 `true`，保留 `false` 以兼容仅跑报告不存库。
- **投递 Tracker 与闭环（阶段 2）**
  - `src/db/application_repo.py`：create、get_by_id、list_applications（按公司/状态/时间筛选）、update、delete、create_from_analysis（从分析预填）。
  - Streamlit「投递记录」Tab：列表展示、新增投递（可勾选「使用本次分析结果预填」）、从概览页「将本次分析记为一次投递」按钮写入并关联 analysis_id。
- **统计与策略评估（阶段 3）**
  - `application_repo.get_stats(from_date, to_date)`：投递数、回复数、面试数、Offer 数及比率。
  - `src/analysis/strategy_summary.py`：`run_strategy_summary(stats_text, history_summary)` 调用 LLM 生成策略建议；prompt 见 `STRATEGY_SUMMARY_SYSTEM_PROMPT`。
  - Streamlit「投递记录」页：统计周期（7 天/30 天/全部）、指标卡片、「生成策略建议」按钮展示文案。
- **文档与配置（阶段 4）**
  - `docs/report_to_db_mapping.md`：补充 analyses 新字段与 applications 表结构及与分析的关联；入库顺序与 main/app 入库说明。
  - `.env.example`：SAVE_TO_DB 默认 true、TAVILY_FILTER_RESULTS 注释。

---



## 2026-03-12：LangGraph + 大牛辩论版本

### 核心架构状态

- **整体流程（LangGraph）**
  - `parse_jd` / `parse_resume`：规则版解析，仅抽取基础字段，报告中直接输出 JD / 简历原文；
  - `tavily_search`：基于 JD 中的公司、岗位、领域关键词构造查询计划，并调用 Tavily 获取公司/岗位/面试/行业情报；
  - `match_core`：规则层维度评分（skills / experience / domain / education / soft_skills_signal 等） + 句子级语义对齐（JD 职责/要求 ↔ 简历句子，仅输出“证据对”不打分）；
  - `debate_*`：多个大牛 persona（王川、Naval、特朗普等）并行基于 JD 原文、简历原文、Matching 数据、Tavily 情报给出对“候选人”的评估和建议；
  - `debate_summary`：主持人节点对所有大牛发言做合议，总结整体结论、要点和推荐策略。

- **匹配策略**
  - 不再使用 LLM 直接给出匹配分数或结论；
  - 匹配阶段只负责：
    - 输出规则层维度分 + gaps（供后续参考）；
    - 输出句子级对齐结果，作为“大牛辩论”的数据基础；
  - 所有“是否值不值得投/有多大机会”等结论全部由后置的大牛辩论层负责。

- **报告体系（`main.py`）**
  - `jd_analysis.md`：JD 原文 + 基础字段展示；
  - `resume_analysis.md`：简历原文；
  - `tavily_report.md`：Tavily 搜索策略（公司/岗位/面试/行业）+ 摘要与链接；
  - `matching_report.md`：规则层维度分、gaps、句子级语义对齐结果（职责/要求 ↔ 简历句子）；
  - `debate_report.md`：各大牛个人结论 + 合议总结。

- **前端 Web UI（`app.py`）**
  - 直接调用 LangGraph 的 `run_analysis`，复用完整流水线；
  - 页面分为：原文与基础信息 → Tavily 情报 → Matching 数据 → 大牛辩论与合议；
  - 不再在前端展示简历重写功能（简历重写能力仍保留在后端模块中，未来可按需接回）。

- **配置与可扩展性**
  - 通过 `.env` 控制：
    - `TAVILY_API_KEY`：是否实际调用 Tavily；
    - `DEBATE_PERSONAS`：启用哪些大牛角色（如 `wangchuan,naval,trump`）；
    - 以及常规的 `GEMINI_MODEL`、`FORCE_REANALYZE`、`SAVE_TO_DB` 等。

- **OCR 模块（统一使用 Flash 多模态，2026-03-14）**
  - **图片与 PDF 均改为调用当前环境配置的 Flash 模型**（`GEMINI_MODEL`，如 `gemini-3-flash-preview`）进行文字提取，经 Files API 上传后由多模态模型识别，统一作用于 JD 与简历的 `parse_jd` / `parse_resume` 输入。
  - 不再依赖本地 Tesseract、EasyOCR 或 pdfplumber；需配置 `GEMINI_API_KEY` 与 `GEMINI_MODEL`。
  - 入口仍为 `extract_text_auto(path)`，内部统一走 `extract_text_with_flash(path)`；`extract_text_from_pdf`、`extract_text_from_image` 也均改为调用 Flash。

- **input 目录与前端入口（2026-03-14）**
  - 新增 **input** 目录作为 JD 与简历的**唯一入口**：文件名含 `jd`/`job` 的为 JD，含 `resume`/`cv` 的为简历，支持 PDF 与常见图片格式。
  - **命令行**：`main.py` 仅从 input 目录读取；`example_data` 已加入 `.gitignore`，不再作为入口。
  - **前端（Streamlit）**：默认使用 input 目录中的文件；上传文件会替代对应项，支持拖拽上传；左侧栏改为「开始分析步骤」指引，input 路径以泛用描述展示。

- **大牛辩论扩展：人物库 + 看好/看空（2026-03-14）**
  - **人物库**（`src/llm/debate_personas.py`）：大牛统一配置为 id / display_name / category / description。分类：政界、商界、虚构、历史。新增可选人物：elon_musk、毛泽东、安妮·杜克、Jordan Peterson、Dan Koe、黄铮、张一鸣、马化腾、马云、习近平、金正恩、池大为、丁元英、苏格拉底、马克思、韦伯等；`.env` 中 `DEBATE_PERSONAS` 为逗号分隔的 id 列表。
  - **随机立场**：每次引入大牛时 50% 概率「看好」、50%「看空」，该次发言的 verdict 与 analysis 须符合该立场；结果中带 `stance` 字段，报告与前端展示立场。
  - **流水线**：由原三节点（wangchuan/naval/trump）改为单节点 `debate_all` 按 env 顺序依次调用，再 `debate_summary` 合议。

---

## 下一步计划（Backlog）

> 以下为下一阶段重点方向，尚未实现。当前 V1 已完成缓存、投递 Tracker、DB 去重与 Web 端配置入口，本节主要关注后续演进。

---

### 一、前端体验与可视化升级

| 目标 | 实现要点 |
|------|----------|
| **更丰富的可视化** | 在 Streamlit 中增加简易图表（如按周/月的投递/回复/面试/Offer 趋势折线图，按公司/岗位聚合的条形图），帮助更直观地查看投递表现。 |
| **分析结果导览优化** | 在 Web 分析结果页增加「一键跳转」入口到对应的 Markdown 报告（本地路径或下载），并提供「复制摘要」按钮，方便粘贴到笔记或 Notion。 |

**依赖**：现有 Tracker 数据模型与分析报告体系已就绪，仅需前端增强。

---

### 二、数据库维护与索引优化

| 目标 | 实现要点 |
|------|----------|
| **索引与查询优化** | 根据常用查询（按时间、按公司、按岗位、按状态）为 `analyses` 和 `applications` 添加合理索引，避免数据规模上来后查询变慢。 |
| **简单归档策略** | 设计按时间归档历史分析与投递记录的方案（如导出为 JSON/CSV 并从主库软删除），保持主库体量可控。 |

**依赖**：基于当前表结构，可逐步扩展，不影响现有功能。

---

### 三、投递数据整理与策略分析（进阶版）

| 目标 | 实现要点 |
|------|----------|
| **多维度统计（进阶）** | 在现有整体统计基础上，增加按公司、岗位类型、城市等维度的拆分统计，并支持按时间区间过滤。 |
| **策略分析增强** | 在 `run_strategy_summary` 的基础上，进一步融合 `analyses` 中的大牛合议结论与 Tavily 情报，让策略建议更贴合「哪些岗位/公司更适合继续投递」。 |

**依赖**：依赖 Tracker 数据与历史分析数据的进一步积累；需微调 prompt 与统计结构。

---

### 四、前后端分离与长远演进

| 目标 | 实现要点 |
|------|----------|
| **前端技术栈迁移（可选）** | 将当前 Streamlit UI 渐进式迁移到 npm 技术栈（如 React/Vue + 组件库），将现有分析/投递/统计/策略接口封装为独立 API（FastAPI 等），提升长期维护性。 |
| **多用户与鉴权** | 在现有单用户基础上引入简单用户体系（auth + user_id），使不同用户的简历/分析/投递记录互相隔离，为未来多端使用做准备。 |

**依赖**：需要先稳定现有 CLI + Web 行为，并为 API 层抽象预留接口；前端迁移可作为后续独立项目推进。
