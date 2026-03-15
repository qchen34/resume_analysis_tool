# Resume Analysis Tool

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)]()
[![LangGraph](https://img.shields.io/badge/LangGraph-Orchestration-222222?logo=graph&logoColor=white)]()
[![Streamlit](https://img.shields.io/badge/Streamlit-Web_UI-FF4B4B?logo=streamlit&logoColor=white)]()
[![Gemini](https://img.shields.io/badge/Google_Gemini-LLM-4285F4?logo=google&logoColor=white)]()
[![Tavily](https://img.shields.io/badge/Tavily-Search_API-000000)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

基于规则 + LLM 的简历与 JD 对比、匹配数据抽取与多角色辩论分析工具。当前版本强调：**解析阶段尽量不用 LLM、匹配阶段只输出“原始数据（不打分）”、分析解读全部交给后置的大牛辩论节点完成**，并支持 Tavily 公司/岗位/行业情报与本地数据库入库。

---

## 功能概览

| 功能模块 | 说明 |
|----------|------|
| **JD / 简历解析** | 规则抽取（公司名、岗位名、地点等），报告中输出原文；图片/PDF 通过 Gemini Flash 多模态 OCR 提取文本，入口为 `input/` 或页面上传。 |
| **Tavily 情报搜索** | 基于解析出的公司、岗位、领域关键词调用 Tavily（或仅生成查询计划），获取公司介绍、岗位职责、面试经验与难度、行业/技术大环境等情报。 |
| **匹配数据（不打分）** | 规则层维度（技能、经验、领域、教育、软能力）与差距列表 `gaps` 作为原始信号；句子级语义对齐（Gemini）为 JD 每条职责/要求匹配简历句子，仅输出证据对，不做评分。 |
| **大牛辩论与合议** | 可配置多角色 persona（如王川、Naval、特朗普等），从不同视角基于 JD/简历/匹配/Tavily 给出竞争力判断；每人随机看好/看空；主持人节点合议总结结论与推荐策略。 |
| **报告与入库** | 按时间戳生成 Markdown 报告（JD、简历、Tavily、Matching、大牛辩论）；可选将 Job / Resume / Analysis 写入 SQLite。 |

---

## 项目结构

```text
resume_analysis_tool/
├── input/                  # JD 与简历统一入口（文件名含 jd/job、resume/cv，支持 PDF/图片）
├── src/
│   ├── models/             # Pydantic 模型：ResumeProfile, JobProfile, MatchingResult 等
│   ├── parsers/            # JD 解析、简历解析（规则版，不调用 LLM）
│   ├── llm/                # Gemini 封装、Prompt 模板、大牛人物库（debate_personas）
│   ├── analysis/           # 匹配引擎（规则+语义对齐）、Tavily 搜索、简历重写（可选）
│   ├── graph/              # LangGraph 流水线（解析 → Tavily → 匹配 → 大牛辩论 → 合议）
│   ├── ocr/                # 图片/PDF 文字提取（Flash 多模态）
│   └── db/                 # SQLAlchemy、init_db、analysis_run_repo
├── tests/                  # 单元测试
├── docs/                   # 文档与 CHANGELOG
├── test_outputs/           # 每次运行生成的报告（按时间戳分子目录）
├── data/                   # SQLite 数据库（.gitignore）
├── main.py                 # 命令行入口：解析 → Tavily → 匹配 → 大牛辩论 → 报告（可选入库）
├── app.py                  # Streamlit Web 入口
├── requirements.txt
└── .env.example
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

将 JD、简历放入项目根目录下的 **`input/`**（文件名需含 `jd` 或 `job`、`resume` 或 `cv`，支持 PDF 与常见图片格式），然后执行：

```bash
python main.py
```

流程：从 input 读取 JD/简历 → Flash 模型 OCR 提取文本 → 规则解析 → Tavily 情报搜索 → 匹配分析（规则层 + 句子级语义对齐，使用 Gemini）→ 大牛辩论与合议（每人随机看好/看空）→ 生成 Markdown 报告。报告输出到 `test_outputs/<YYYYMMDD_HHMMSS>/`。终端会打印每步使用的 API/模型。

### 4. 启用数据库（可选）

```bash
# 建表（首次）
python -m src.db.init_db
```

在 `.env` 中设置 `SAVE_TO_DB=true`，再次运行 `python main.py` 后会将当次分析的 Job、Resume、Analysis（含匹配原始数据与大牛辩论结果）写入 `data/resume_analysis.db`。

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

启动方式（建议在项目 venv 下执行）：

```bash
python3 -m streamlit run app.py
```

按页面左侧「开始分析步骤」操作：准备文件（input 或上传）→ 解析 → 选择大牛 → 开始分析。若遇 `extra_items` 等报错，请用当前 venv 的 Python 启动并安装 `typing_extensions>=4.13`。

---

## 上传到 GitHub

若尚未在 GitHub 建仓，可参考 [docs/upload_to_github.md](docs/upload_to_github.md)：创建空仓库后，在项目根目录执行 `git init`、`git add .`、`git commit`、`git remote add origin <URL>`、`git push -u origin main`。`.gitignore` 已排除 `.env`、`data/`、`venv/`、`example_data/`、`test_outputs/*` 等。

---

## 技术栈

- Python 3.x、Pydantic、SQLAlchemy、Typer、LangGraph
- Google Gemini API（`google-genai`）：用于匹配阶段的句子级语义对齐和大牛辩论/合议
- Tavily（`tavily-python`）：公司/岗位/行业情报搜索
- Streamlit：Web 前端展示分析过程与结果
- 配置：`python-dotenv`，环境变量见 `.env.example`

---

## Star History（示例）

[![Star History Chart](https://api.star-history.com/svg?repos=qchen34/resume_analysis_tool&type=Date)](https://star-history.com/#qchen34/resume_analysis_tool&Date)

---

## License

本项目采用 **MIT License** 开源许可协议，允许在保留版权和许可声明的前提下自由使用、修改和分发。  
如需在公司内部或商业项目中集成本工具，请遵守 MIT 协议要求。
