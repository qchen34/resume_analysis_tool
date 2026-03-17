# 报告数据与数据库映射说明

本文档说明：当前各输出报告中有哪些数据适合入库、对应哪张表、如何存储。

---

## 一、报告与数据来源

| 报告文件 | 数据来源（程序内变量） |
|----------|------------------------|
| `jd_analysis.md` | `jd_text`, `job_profile`, `job_json` |
| `resume_analysis.md` | `resume_text`, `resume_profile`, `resume_json` |
| `matching_report.md` | `matching_result`, `matching_refined`（含 responsibility_coverage、skill_coverage） |
| `rewritten_resume.md` | `rewrite_result.revised_resume_text` |
| `rewrite_report.md` | `rewrite_result.changes` |

---

## 二、适合入库的数据与表映射

### 1. jobs 表

| 入库字段 | 来源 | 存储方式 |
|----------|------|----------|
| `raw_jd_text` | 原始 JD 全文（如 `jd_text`） | TEXT，原样存储 |
| `job_profile_json` | `JobProfile` 结构化结果 | JSON：`job_profile.model_dump(mode="json")` |
| `title` | `job_profile.role_title` | String，可选 |
| `company` | `job_profile.company` | String，可选 |
| `level` | `job_profile.level` | String(50)，可选 |
| `location` | `job_profile.location` | String，可选 |
| `created_at` | 插入时间 | DateTime，默认当前时间 |

**说明**：每次「分析运行」可插入一条新 Job；若需按 JD 内容去重复用，可在业务层用 `raw_jd_text` 或内容哈希做查询后再决定 insert 或复用。

---

### 2. resumes 表

| 入库字段 | 来源 | 存储方式 |
|----------|------|----------|
| `raw_resume_text` | 原始简历全文（如 `resume_text`） | TEXT，原样存储 |
| `resume_profile_json` | `ResumeProfile` 结构化结果 | JSON：`resume_profile.model_dump(mode="json")` |
| `user_id` | 若与用户体系打通 | ForeignKey，可选 |
| `created_at` | 插入时间 | DateTime，默认当前时间 |

**说明**：同上，可按需按内容去重或每次插入新记录。

---

### 3. analyses 表（一次「简历 + JD」匹配分析）

| 入库字段 | 来源 | 存储方式 |
|----------|------|----------|
| `resume_id` | 本次分析使用的简历记录 id | ForeignKey → resumes.id |
| `job_id` | 本次分析使用的 JD 记录 id | ForeignKey → jobs.id |
| `user_id` | 若与用户体系打通 | ForeignKey，可选 |
| `matching_result_json` | 完整匹配结果，供报告回溯与二次分析 | JSON：`matching_result.model_dump(mode="json")`，内含 overall_score、dimensions、gaps 等 |
| `overall_score` | 综合匹配分（0–100），便于排序/筛选 | Integer，可从 `matching_result.overall_score` 取整存入；若为 None 可存 NULL |
| `user_competency_tags` | 用户自填或后续打标签 | JSON，可选 |
| `debate_rounds_json` | 大牛辩论各轮结果（含 persona、verdict、stance、analysis 等） | JSON 数组，可选 |
| `tavily_insights_json` | Tavily 搜索计划与结果摘要 | JSON，可选 |
| `final_competitiveness_json` | 合议总结（overall_verdict、summary_points、suggested_strategy） | JSON，可选 |
| `created_at` | 分析完成时间 | DateTime，默认当前时间 |

**说明**：`matching_report.md` 中的核心内容应完整放入 `matching_result_json`。`debate_rounds_json` / `tavily_insights_json` / `final_competitiveness_json` 便于历史回溯与投递记录关联时回查分析结论。

---

### 4. rewritten_resumes 表（每次重写一条）

| 入库字段 | 来源 | 存储方式 |
|----------|------|----------|
| `analysis_id` | 对应的分析记录 id | ForeignKey → analyses.id |
| `revised_resume_text` | 重写后的简历全文 | TEXT |
| `changes_json` | 变更清单，与报告中的「变更清单」一致 | JSON：`{"changes": [{"section", "item_index", "change_type", "old_text", "new_text"}, ...]}` |
| `llm_model` | 使用的模型标识（如 GEMINI_MODEL） | String(100)，可选 |
| `created_at` | 重写完成时间 | DateTime，默认当前时间 |

**说明**：对应 `rewritten_resume.md` 的正文与 `rewrite_report.md` 的变更清单；一次分析可对应多次重写（不同策略或模型），故为 1:N。

---

### 5. applications 表（投递记录 / 投递 Tracker）

| 入库字段 | 说明 |
|----------|------|
| `id` | 主键 |
| `analysis_id` | 关联的分析记录 id（ForeignKey → analyses.id），可空，支持先记投递后补分析 |
| `company` | 公司名称 |
| `role_title` | 职位名称 |
| `jd_summary_or_link` | JD 摘要或链接 |
| `location` | 地点 |
| `salary_range` | 薪资范围 |
| `platform` | 投递平台（如 Boss、拉勾） |
| `initiated_contact` | 是否主动沟通 |
| `resume_sent` | 是否已投递简历 |
| `has_reply` | 是否有回复 |
| `has_interview` | 是否邀约面试 |
| `interview_rounds` | 面试轮次 |
| `interview_feedback` | 面试反馈 |
| `offer_details` | Offer 详情 |
| `created_at` / `updated_at` | 创建/更新时间 |

**说明**：一条分析可对应多条投递记录（同一岗位多次跟进等）。通过 `analysis_id` 可回查当次分析的匹配与辩论结论。仓储层见 `src.db.application_repo`。

---

## 三、入库顺序与依赖

1. **Job**：无依赖，先插入，得到 `job_id`。  
2. **Resume**：无依赖（或依赖 `user_id`），插入后得到 `resume_id`。  
3. **Analysis**：依赖 `resume_id`、`job_id`，插入后得到 `analysis_id`；写入时把 `matching_result`（及可选 `matching_refined` 中额外字段）序列化进 `matching_result_json`。  
4. **RewrittenResume**：依赖 `analysis_id`，插入重写结果与 `changes_json`。  
5. **Application**：可选，依赖 `analysis_id`（可空）；由用户在前端或 CLI 在「分析完成」后创建，或单独新增投递记录。

---

## 四、JSON 存储格式约定

- **job_profile_json / resume_profile_json**：与 Pydantic `JobProfile` / `ResumeProfile` 的 `model_dump(mode="json")` 一致，日期等用可序列化类型（如 ISO 字符串）。  
- **matching_result_json**：与 `MatchingResult.model_dump(mode="json")` 一致；若需包含 responsibility_coverage、skill_coverage，在写入前将二者合并进同一 dict 再序列化。  
- **changes_json**：`{"changes": [ResumeChange.model_dump(), ...]}`，与 `rewrite_report` 中变更清单一一对应。

---

## 五、如何在流程中入库

### 方式一（推荐）：统一入库函数

在「分析 + 重写」流程末尾调用 `src.db.analysis_run_repo.save_analysis_run`，一次性写入 Job、Resume、Analysis、RewrittenResume（若有）。

```python
from src.db.analysis_run_repo import save_analysis_run

job_id, resume_id, analysis_id, rewritten_id = save_analysis_run(
    jd_text=jd_text,
    resume_text=resume_text,
    job_profile=job_profile,
    resume_profile=resume_profile,
    matching_result=matching_result,
    matching_refined=matching_refined,
    rewrite_result=rewrite_result,
    user_id=None,
    llm_model=os.getenv("GEMINI_MODEL"),
)
# 可将 analysis_id 写入报告或日志，便于回溯
```

- **main.py**：默认 `SAVE_TO_DB=true`，脚本会在生成报告后调用上述函数并打印 `analysis_id`；设 `SAVE_TO_DB=false` 可跳过入库。
- **app.py**：Web 分析完成后若 `SAVE_TO_DB` 为 true，同样会调用 `save_analysis_run`（含 `debate_rounds`、`tavily_insights`、`final_competitiveness`），并将 `last_analysis_id` 存入 session，供「将本次分析记为一次投递」使用。
- 入库前请确保已执行 `python -m src.db.init_db` 完成建表。若已有旧库，新增的 `analyses` 列与 `applications` 表需通过迁移或重新建表生效。

### 方式二：单表写入

先自行创建并取得 `job_id`、`resume_id`、`analysis_id`，再调用 `src.db.rewritten_resume_repo.save_rewritten_resume(analysis_id, result)` 等。

---

报告中的 Token 使用等统计信息当前未单独建表，如需可后续在 analyses 上增加 JSON 字段（如 `llm_usage_json`）统一存入。
