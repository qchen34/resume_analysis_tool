# CHANGELOG / 项目阶段记录

> 本文件用于记录 Resume Analysis Tool 在不同阶段的**架构演进、主要改动**以及**下一步计划**，方便后续回顾与协作。

---

## 2026-03-12：LangGraph + 大牛辩论版本（当前阶段）

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

- **报告体系（`test_script.py`）**
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

---

## 下一步计划（Backlog）

> 以下为下一阶段重点方向，尚未实现，按粗略优先级排序。

1. **图片 JD / 图片简历解析**
   - 支持上传 JD 截图或拍照版简历（PDF/JPEG/PNG 等），通过 OCR + 结构化规则/LLM 提取文本；
   - 与现有文本 JD / 简历解析模块打通，使整条流水线对“图片/文本”输入透明；
   - 需要评估：本地 OCR vs 外部 API，及成本与隐私权衡。

2. **前端 Web UI 优化**
   - 视觉与交互层面：
     - 将当前多段文本展示升级为更清晰的卡片 / 时间线 / 分栏布局；
     - 为 Tavily 结果、Matching 数据、辩论结论增加简洁的视觉标签（如风险/机会/建议）。
   - 使用体验：
     - 增加“导出分析结果为 Markdown/JSON”的按钮；
     - 支持多次分析历史在前端快速切换查看；
     - 视图上更清晰地区分“数据层”（matching_refined 等）与“观点层”（大牛辩论）。

3. **大牛角色扩展与多轮辩论机制**
   - 新增更多 persona（示例）：
     - `elon_musk`：偏极端创新和冒险视角，看“技术野心和执行力”；
     - `mao_zedong`：从组织战、长期斗争和群众基础角度看候选人；
     - `chi_dawei`（池大为）、`ding_yuanying`（丁元英）：从商业博弈、结构性机会、资源配置视角点评；
   - 为不同 persona 设计更细致的 prompt 风格模板（乐观/悲观、宏观/微观、技术/业务等维度）；
   - 引入多轮辩论：
     - Round 1：各自独立的一审观点；
     - Round 2：看到其他大牛的观点后，允许更新或修正自己的判断（例如指出他人遗漏/过度乐观）；
     - Round 3（可选）：针对争议点做小范围“复盘”，再交由主持人总结；
   - 技术上可通过：
     - LangGraph 中增加多轮 `debate_*` 节点和中间 state（例如 `debate_rounds_round1` / `round2`）；
     - Prompt 层明确说明当前轮次是否可以引用上一轮的其他嘉宾观点，并控制不互相“抄答案”。

---

> 若未来有新的大版本架构调整（例如：引入向量检索知识库、支持多 JD/多候选人对比等），请在此文件继续 append 版本小节，并简要记录动机与关键改动。  
