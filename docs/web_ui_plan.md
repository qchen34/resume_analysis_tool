# 前端网页展示规划

目标：用网页实现「输入简历 → 输入 JD → 展示 Matching Report → 展示 Resume Rewrite」的完整流程，复现当前 `main.py` 的核心步骤。

---

## 一、技术选型

| 方案 | 优点 | 缺点 |
|------|------|------|
| **Streamlit**（推荐） | 与现有 Python 代码同仓、直接 `import` 调用解析/匹配/重写；无需单独后端与 API；开发快、部署简单（`streamlit run app.py`） | 交互形态偏「表单+重跑」，多页需用 `st.session_state` 或多页 app |
| FastAPI + React/Vue | 前后端分离、可做更复杂交互与样式 | 需维护 API 层、前端工程与部署，工作量更大 |

**建议**：优先用 **Streamlit** 做一版可用的 Web 展示；若后续要更定制化的 UI 再考虑 FastAPI + 前端。

---

## 二、页面流程（步骤）

```
┌─────────────────────────────────────────────────────────────────┐
│  Step 1：输入                                                    │
│  - 文本框或上传：JD 原文                                         │
│  - 文本框或上传：简历原文                                        │
│  - [开始分析] 按钮                                               │
└─────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────┐
│  Step 2：解析结果（可选折叠展示）                                 │
│  - JD 解析：岗位、公司、必备/加分技能、职责摘要等                 │
│  - 简历解析：姓名、教育、经历条数、技能摘要等                     │
└─────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────┐
│  Step 3：Matching Report                                         │
│  - 综合匹配分（0–100）+ 简要说明                                 │
│  - 各维度得分（skills / experience / domain / education / 软能力）│
│  - 维度解释（dimension_explanations）                            │
│  - 差距列表（gaps：类型、名称、严重程度、说明）                   │
│  - 可选展开：职责覆盖度、任职要求覆盖度（JSON 或表格）            │
└─────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────┐
│  Step 4：Resume Rewrite                                          │
│  - 修改后简历全文（可复制 / 下载）                               │
│  - 变更清单：每条「原文 → 修改后」对照                           │
└─────────────────────────────────────────────────────────────────┘
```

- 实现方式可以是：**单页从上到下** 依次展示 Step 1 → 2 → 3 → 4（分析完成后一次性展开），或 **Tab**：Tab1 输入、Tab2 解析、Tab3 Matching、Tab4 Rewrite。
- 建议：单页流式，分析时用 `st.spinner` 提示当前步骤（解析 JD / 解析简历 / 匹配分析 / 简历重写），完成后在下方用 **expander** 或 **section** 展示各块，便于折叠。

---

## 三、数据流与后端复用

- **输入**：页面收集 `jd_text`、`resume_text`（字符串）。
- **调用顺序**（与 `main.py` 一致）：
  1. `parse_jd(jd_text)` → `JobProfile`, `job_json`
  2. `parse_resume(resume_text)` → `ResumeProfile`, `resume_json`
  3. `compute_matching_with_details(resume_profile, job_profile)` → `MatchingResult`, `matching_refined`
  4. `rewrite_resume_for_job(resume_text, job_profile, matching_result)` → `RewriteResult`
- **可选**：若需入库，在 4 之后调用 `save_analysis_run(...)`（可做成页面勾选「保存到数据库」）。
- 所有逻辑直接 **import 现有模块**，无需新建 REST API（Streamlit 与后端同进程）。

---

## 四、展示内容明细

### Step 1：输入

- **JD**：`st.text_area("JD 原文", height=200)` 或 `st.file_uploader("上传 JD 文件", type=["md","txt"])`
- **简历**：同上。
- 校验：两者均非空再允许点击「开始分析」。

### Step 2：解析结果（简要）

- **JD**：`job_profile.role_title`、`company`、`must_have_skills`（前几条）、`responsibilities`（前 3 条）。
- **简历**：`resume_profile.name`、教育条数、工作经历条数、技能摘要。
- 用 `st.expander("查看解析详情")` 折叠，内可放 `job_json` / `resume_json` 或结构化展示。

### Step 3：Matching Report

- **总分**：`matching_result.overall_score`（0–100）+ `matching_result.explanation`（一段话）。
- **维度分**：`matching_result.dimensions` → 进度条或数字卡片（skills, experience, domain, education, soft_skills_signal, leadership, communication, culture_fit）。
- **维度解释**：`matching_result.dimension_explanations` → 按维度名展示短文。
- **差距列表**：`matching_result.gaps` → 表格或列表，列：type, name, severity, detail。
- **职责/任职覆盖**：`matching_refined["responsibility_coverage"]`、`matching_refined["skill_coverage"]` → 可折叠表格或 JSON。

### Step 4：Resume Rewrite

- **修改后全文**：`rewrite_result.revised_resume_text` → `st.text_area(..., value=..., height=400)` 只读，或 `st.markdown` 渲染（若为 Markdown）。
- **变更清单**：`rewrite_result.changes` → 每条 `section`、`change_type`、`old_text`、`new_text`，可用 `st.expander` 或表格展示「原文 / 修改后」对照。
- 可选：提供「下载 .md」按钮，将 `revised_resume_text` 写入文件供下载。

---

## 五、项目结构建议

```text
resume_analysis_tool/
├── app.py                    # Streamlit 入口（新建）
├── src/                      # 现有代码，不变
├── requirements.txt          # 增加 streamlit
└── docs/
    └── web_ui_plan.md         # 本文档
```

- **运行**：`streamlit run app.py`（默认 http://localhost:8501）。
- **环境**：与现有一致，需配置 `.env`（`GEMINI_API_KEY` 等）；在 `app.py` 开头 `load_dotenv()`，与 `main.py` 一致。

---

## 六、实施顺序建议

1. **新建 `app.py`**：仅 Step 1（两个 text_area + 按钮），点击后占位打印「待接入」。
2. **接入解析**：点击后依次调用 `parse_jd`、`parse_resume`，用 spinner 包裹，再在下方展示 Step 2 解析摘要。
3. **接入匹配**：调用 `compute_matching_with_details`，展示 Step 3（总分、维度、gaps、explanation、dimension_explanations）。
4. **接入重写**：调用 `rewrite_resume_for_job`，展示 Step 4（修改后全文 + 变更清单）。
5. **可选**：职责/任职覆盖度展开、下载报告、入库勾选。

按此顺序可每步验证，最终与当前 `main.py` 行为一致，并在网页上完成「输入 → Matching Report → Resume Rewrite」的完整展示。
