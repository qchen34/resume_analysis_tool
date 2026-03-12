# Resume Analysis Tool

基于规则 + LLM 的简历与 JD 对比、匹配数据抽取与多角色辩论分析工具。当前版本强调：**解析阶段尽量不用 LLM、匹配阶段只输出“原始数据（不打分）”、分析解读全部交给后置的大牛辩论节点完成**，并支持 Tavily 公司/岗位/行业情报与本地数据库入库。

---

## 功能概览

- **JD / 简历解析（规则占位版）**：当前阶段不再用 LLM 做结构化解析，只做非常轻量的规则抽取（如公司名、岗位名、地点），报告中直接输出 JD / 简历原文，便于人工阅读。
- **Tavily 情报搜索**：基于解析出的公司、岗位、领域关键词，调用 Tavily（或仅生成查询计划），获取公司介绍、岗位职责、面试经验与难度、行业/技术大环境等情报。
- **匹配数据（不打分）**：
  - 规则层维度打分（技能、经验、领域、教育、软能力）与差距列表 `gaps`，作为**原始信号**；
  - 句子级语义对齐（使用 Fast 模型）：为 JD 每一条职责/任职要求找到简历中可能对应的句子，仅生成“证据对”，不做任何评分或结论。
- **大牛辩论与合议（核心分析层）**：
  - 多个可配置的大牛 persona（如王川、Naval、特朗普），从不同 HR/用人方视角出发，基于 JD 原文、简历原文、匹配数据与 Tavily 情报，给出对“候选人”的竞争力判断与建议；
  - 大牛角色和语气可通过 `.env` 中的 `DEBATE_PERSONAS` 控制；某些角色偏谨慎/悲观、某些偏长期乐观，整体风格接地气、贴合当前“行情偏冷、HC 紧缩”的市场环境；
  - 最终由一个“主持人”节点对各大牛观点做合议，总结整体结论、关键要点和推荐策略。
- **报告与入库**：按时间戳生成 Markdown 报告（JD 分析、简历分析、Tavily 报告、Matching 报告、大牛辩论报告）；可选将 Job / Resume / Analysis（含匹配与辩论结果）写入 SQLite。

---

## 项目结构

```text
resume_analysis_tool/
├── example_data/           # 示例 JD、简历（可替换为自己的 md/txt）
├── src/
│   ├── models/             # Pydantic 模型：ResumeProfile, JobProfile, MatchingResult, RewriteResult 等
│   ├── parsers/            # JD 解析、简历解析（当前为轻量规则版，占位，不再调用 LLM）
│   ├── llm/                # Gemini 封装（client）、Prompt 模板（prompts，包括大牛辩论/合议）
│   ├── analysis/           # 匹配引擎（规则层 + 句子级语义对齐）、Tavily 搜索封装、简历重写（暂从主流程移除）
│   ├── db/                 # SQLAlchemy 表、init_db、analysis_run_repo、rewritten_resume_repo
│   └── cli/                # Typer 入口（当前为占位，主流程见 test_script.py）
├── tests/                  # 单元测试
├── docs/                   # 文档：上传 GitHub、报告与数据库映射
├── test_outputs/           # 每次运行生成的报告（按时间戳分子目录）
├── data/                   # SQLite 数据库文件（.gitignore）
├── test_script.py          # 一键运行：解析 → Tavily → 匹配（规则+语义对齐，仅数据） → 大牛辩论 → 报告（可选入库）
├── requirements.txt
├── .env.example
└── project_build_steps.md  # 构建步骤与产品思路
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

在 `.env` 中至少配置：

| 变量 | 说明 |
|------|------|
| `GEMINI_API_KEY` | Gemini API 密钥 |
| `GEMINI_MODEL` | Fast 模型（用于匹配阶段的句子级语义对齐等），如 `gemini-3-flash-preview` |
| `TAVILY_API_KEY` | Tavily 搜索 API 密钥，用于公司/岗位/行业情报搜索（留空则只输出搜索计划，不触网） |

其他重要变量（见 `.env.example` 注释）：

- `DATABASE_URL`：SQLite 等数据库连接串；
- `FORCE_REANALYZE`：是否忽略缓存，强制重新调用 LLM / Tavily；
- `SAVE_TO_DB`：是否在分析结束后写入数据库；
- `DEBATE_PERSONAS`：参与辩论的大牛列表（逗号分隔，例如 `wangchuan,naval,trump`），可通过删减/调整顺序组合不同评审风格；
- `ENABLE_REWRITE_REVIEW`：与简历重写相关，当前主流程默认不调用重写，仅保留能力。

### 3. 运行一次完整分析（CLI）

使用示例数据（需在 `example_data/` 下放置 `jd_example_1.md`、`resume_example_1.md`，或修改 `test_script.py` 中的路径）：

```bash
python test_script.py
```

流程：读取 JD 与简历 → 规则版解析（仅抽取基础字段，报告中输出原文）→ Tavily 情报搜索 → 匹配分析（规则层 + 句子级语义对齐，仅输出数据，不打分）→ 多位大牛辩论与合议 → 生成 Markdown 报告（JD/Tavily/Matching/Debate）→ 可选写入数据库。报告输出到 `test_outputs/<YYYYMMDD_HHMMSS>/`。

### 4. 启用数据库（可选）

```bash
# 建表（首次）
python -m src.db.init_db
```

在 `.env` 中设置 `SAVE_TO_DB=true`，再次运行 `python test_script.py` 后会将当次分析的 Job、Resume、Analysis（含匹配原始数据与大牛辩论结果）写入 `data/resume_analysis.db`。

---

## 报告与数据

- **报告文件（按运行一次生成一个时间戳目录）**：
  - `jd_analysis.md`：JD 原文 + 基础信息；
  - `resume_analysis.md`：简历原文；
  - `tavily_report.md`：Tavily 搜索策略（按公司/岗位/面试/行业分类）+ 摘要与链接；
  - `matching_report.md`：规则层维度得分与差距列表 + 句子级语义对齐原始 JSON/表格；
  - `debate_report.md`：各大牛个人观点（结论、信心、分析、建议）+ 合议总结。
- **数据库**：表 `jobs`、`resumes`、`analyses`、`rewritten_resumes`；报告数据与表映射见 [docs/report_to_db_mapping.md](docs/report_to_db_mapping.md)。当前主流程仍会写入 `rewritten_resumes` 表（若提供简历重写结果），但 Web/CLI 默认不触发重写。

---

## Web 前端（Streamlit）

- 文件：`app.py`
- 特点：
  - 输入 JD 与简历原文，调用 LangGraph 的 `run_analysis`，完整复用「解析 → Tavily → 匹配 → 大牛辩论」流水线；
  - 页面结构清晰展示：
    - 原文与基础信息；
    - Tavily 公司/岗位/行业情报查询策略与结果；
    - 匹配规则层维度得分与差距列表（原始数据）；
    - 句子级语义对齐结果（职责/任职要求 ↔ 简历句子）；
    - 大牛辩论与合议（每位大牛的观点 + 合议结论和推荐策略）。

启动方式：

```bash
streamlit run app.py
```

---

## 上传到 GitHub

若尚未在 GitHub 建仓，可参考 [docs/upload_to_github.md](docs/upload_to_github.md)：创建空仓库后，在项目根目录执行 `git init`、`git add .`、`git commit`、`git remote add origin <URL>`、`git push -u origin main`。`.gitignore` 已排除 `.env`、`data/`、`venv/`、`test_outputs/*` 等。

---

## 技术栈

- Python 3.x、Pydantic、SQLAlchemy、Typer、LangGraph
- Google Gemini API（`google-genai`）：用于匹配阶段的句子级语义对齐和大牛辩论/合议
- Tavily（`tavily-python`）：公司/岗位/行业情报搜索
- Streamlit：Web 前端展示分析过程与结果
- 配置：`python-dotenv`，环境变量见 `.env.example`
