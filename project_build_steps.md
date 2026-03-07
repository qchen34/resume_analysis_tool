## 项目构建步骤总览

本工具当前阶段的优先范围是：**简历 & JD 对比 + 匹配度评分 + 简历修改并标注修改点**。  
Tavily / BQ / Web UI 暂缓，后续在此基础上扩展。

---

## 一、整体架构与核心变化

- **LLMApiLayer**：在 `core services` 中新增独立的 LLM 调用层，所有“理解”和“生成”类任务都经由此层：
  - 结构化解析：简历解析、JD 解析。
  - 匹配分析：规则打分结果 + 关键特征喂给 LLM，生成更人类友好的解释和维度化评分。
  - 简历重写：根据 JD 与差距信息，生成优化后的简历文本与修改标注。
- **NLP / 关键词分析**：作为 **辅助与 sanity check**，主逻辑由 LLM 产出的结构承载。
- **数据库**：记录每次分析的输入输出（原简历、JD、匹配结果、重写后的简历等），支撑后续回溯与优化。

---

## 二、核心模块职责（聚焦“对比 + 简历修改”）

### 1. LLMApiLayer

- **职责**：
  - 统一封装所有 LLM 调用。
  - 管理 Prompt 模板（解析 / 匹配 / 重写）。
  - 控制参数（temperature、max_tokens、重试、日志等）。
- **典型接口**：
  - `analyze_resume(raw_resume_text) -> ResumeProfileJSON`
  - `analyze_jd(raw_jd_text) -> JobProfileJSON`
  - `score_match(resume_profile, job_profile) -> MatchingScoreJSON`
  - `rewrite_resume(raw_resume_text, job_profile, matching_insights) -> { revised_resume, diff_annotations }`
- **输出格式**：统一使用 **JSON 结构**，再映射到 `ResumeProfile` / `JobProfile` / `MatchingResult` 等数据模型。

### 2. 简历 & JD 解析 Orchestrator（LLM 驱动）

#### ResumeParserOrchestrator

- 输入：`raw_resume_text` + 可选语言/格式信息。
- 步骤：
  - 轻量预处理（去页眉页脚、异常换行等）。
  - 调用 `LLMApiLayer.analyze_resume()`，要求输出严格 JSON。
  - 校验 / 解析 JSON → `ResumeProfile`（缺失字段用默认值并打 log）。
- `ResumeProfile` 结构建议：
  - `basic_info`：`name, email, phone, location`。
  - `education[]`：`{ school, degree, major, start, end, gpa, highlights[] }`。
  - `experiences[]`：`{ company, title, start, end, type, bullets[], tech_stack[] }`。
  - `projects[]`：`{ name, role, start, end, bullets[], tech_stack[], impact_metric? }`。
  - `skills`：`{ languages[], frameworks[], tools[], others[] }`。

#### JDParserOrchestrator

- 输入：`raw_jd_text`。
- 步骤类似，通过 `LLMApiLayer.analyze_jd()` 解析为 `JobProfile`。
- `JobProfile` 结构建议：
  - `role_title, level（校招/社招/中高级）, department`。
  - `must_have_skills[] / nice_to_have_skills[]`。
  - `responsibilities[]`。
  - `domain_keywords[]`（如广告、电商、AI infra 等）。
  - `soft_skills[] / values_keywords[]`。
  - `experience_requirements`（years, type 如后端/算法/数据等）。

> 规则 NLP/关键词分析作为 **fallback/sanity check**，但不作为主决策来源。

---

## 三、匹配度评分：规则 + LLM 结合

### 1. 规则侧：计算基准匹配度

- 技能覆盖度：  
  \`cover = |resume.skills ∩ jd.must_have| / |jd.must_have|\`
- 经验匹配度：  
  是否存在相同/相似岗位 & 行业 & 年限的经历。
- 领域匹配度：  
  `domain_keywords` 在简历与 JD 中的重合度。
- 教育达标度：  
  学历 / 专业是否满足硬性条件。

输出示例 `MatchingBaseResult`：

```python
MatchingBaseResult = {
  "overall_score_raw": 0.0,  # 0.0 ~ 1.0
  "dimensions": {
    "skills": 0.0,
    "experience": 0.0,
    "domain": 0.0,
    "education": 0.0,
    "soft_skills_signal": 0.0,
  },
  "gaps": [
    {"type": "skill", "name": "Redis", "severity": "high"},
    # ...
  ],
}
```

### 2. 专业人士修改简历的一般流程（作为 MatchingEngine 的思路基线）

现实中比较成熟的 JD/简历分析与修改过程，通常并不是简单“数关键词”，而是分几层：

1. **读 JD，划重点**  
   - 划出：硬性条件（年限、学历、领域）、核心职责、高频词（场景/技术/软技能）、加分项。  
   - 在脑中形成一个「理想候选人画像」：要能解决什么类型的问题、在什么环境下工作。

2. **读简历，找“证据”**  
   - 按项目/经历逐段扫描：是否有**直接对口的场景**（例如：智能客服、智能文档、大模型产品等）。  
   - 检查每段经历是否具备：**场景（S）+ 任务（T）+ 行动（A）+ 结果（R）**。  
   - 标记：哪些 bullet 对当前 JD 非常有用（保留/强化）、哪些偏题（弱化/删除）。

3. **硬匹配 vs 软匹配**  
   - 先看硬门槛：年限、技术栈、行业/场景是否踩中最低要求。  
   - 再看软能力：领导力、跨团队协作、数据驱动、owner 意识等。

4. **列差距 & 制定修改策略**  
   - 差距大致分三类：  
     - 简历里**有但写弱了** → 重写/量化。  
     - 有**相邻可类比的经验但没写或写少了** → 补充/重组。  
     - **真实能力/经历缺口** → 作为后续补课方向，不在简历上“捏造”。  
   - 修改时遵守：  
     - 不虚构、不夸大。  
     - 优先对齐 JD 最核心的 3–5 条要求。  
     - 少而精地讲好 2–3 个强相关故事，而不是堆满所有经历。

在本项目中，MatchingEngine 的规则层和 LLM 精炼层，就是将上述「专业顾问的脑内流程」拆解成：  
**特征抽取 → 规则打分 → 差距列表 → 建议策略** 的一套结构化逻辑。

### 3. LLM 侧：精炼说明与维度微调

- 调用 `LLMApiLayer.score_match(resume_profile, job_profile)`：
  - 输入包括：`MatchingBaseResult` + 若干原文片段。
  - 要求输出：
    - `overall_score`（0–100）。
    - 各维度评分（在规则基础上微调）。
    - 人类可读解释（中文说明段落）。
    - `prioritized_gaps[]`：按优先级排序的差距项，用于驱动后续简历重写。

---

## 四、简历重写与修改标注

### 1. ResumeRewriter Orchestrator

- 输入：
  - `raw_resume_text`
  - `JobProfile`
  - `MatchingInsights`（如 `prioritized_gaps` 等）
- 调用 `LLMApiLayer.rewrite_resume()`，Prompt 重点约束：
  - 保留姓名、联系方式等个人信息，不随意篡改。
  - 尽量在原有结构基础上修改，不做完全重排。
  - 输出：
    - `revised_resume_text`：完整新简历文本。
    - `changes`：结构化修改列表。

### 2. 修改标注设计

- **结构化 diff** 示例：

```json
{
  "changes": [
    {
      "section": "experience",
      "item_index": 1,
      "change_type": "edit",
      "old_text": "- 负责接口开发",
      "new_text": "- 独立负责 XXX 核心接口开发，将 P95 延迟降低 30%，服务日均 QPS 1w+"
    },
    {
      "section": "skills",
      "change_type": "add",
      "new_text": "Redis, Kafka"
    }
  ]
}
```

- **文本标记**（让用户一眼看出改动）：
  - 新增：`[[+ 新增内容 +]]`
  - 修改：`[[~ 修改后内容 ~]]`

CLI 报告中可同时提供：

- 「修改建议清单」：按 gap 类型分组列出。
- 「修改后完整简历」：可直接复制使用。

---

## 五、数据库设计（MVP 范围）

- 使用 SQLite 或 Postgres（当前默认 SQLite），核心表如下：

1. **users**
   - `id`
   - `email`（可空）
   - `created_at`

2. **jobs**（JD 记录）
   - `id`
   - `title`
   - `company`
   - `level`（如 intern/junior/mid/senior）
   - `location`
   - `raw_jd_text`（TEXT）
   - `job_profile_json`（JSON，存 `JobProfile`）
   - `created_at`

3. **resumes**
   - `id`
   - `user_id`（可空）
   - `raw_resume_text`（TEXT）
   - `resume_profile_json`（JSON，存 `ResumeProfile`）
   - `created_at`

4. **analyses**（一次「简历-JD 分析」对应一条）
   - `id`
   - `user_id`（可空）
   - `resume_id`
   - `job_id`
   - `matching_result_json`（包含 overall_score、各维度、gaps 等）
   - `overall_score`（单列冗余，方便排序筛选）
   - `user_competency_tags`（JSON，如“AI-PM-中级、平台型产品、出海经验”等）
   - `created_at`

5. **rewritten_resumes**（每次重写产生一条记录）
   - `id`
   - `analysis_id`
   - `revised_resume_text`（TEXT）
   - `changes_json`（结构化 diff）
   - `llm_model`（使用的模型标识，如 gpt-4.1-mini）
   - `created_at`

> 上述字段已经覆盖：原简历、JD、修改后简历、匹配度、用户专业能力等核心信息。

---

## 六、阶段性实施步骤（当前聚焦阶段）

1. **Step 1：数据模型 & DB 建表设计**
   - 定义 `ResumeProfile`、`JobProfile`、`MatchingResult`、`RewriteResult` 等 Pydantic 模型。
   - 使用 SQLAlchemy 建立 `users` / `jobs` / `resumes` / `analyses` / `rewritten_resumes` 数据表。

2. **Step 2：LLMApiLayer & Prompt 模板**
   - 编写 4 类 Prompt：
     - `resume_structured_parse_prompt`
     - `jd_structured_parse_prompt`
     - `match_scoring_prompt`
     - `resume_rewrite_prompt`
   - 实现统一的 LLM 调用封装与基础日志。

3. **Step 3：Resume/JD Orchestrator**
   - 打通从原始文本 → LLM 解析 → `Profile` → 入库的完整流程。

4. **Step 4：MatchingEngine**
   - 实现“规则打分 + LLM 精炼”的组合逻辑，输出 `MatchingResult` 并写入数据库。

5. **Step 5：ResumeRewriter**
   - 基于匹配差距调用 LLM 重写简历，生成结构化 diff，并写入 `rewritten_resumes`。

6. **Step 6：CLI 接口**
   - 打通命令行一键分析流程：
     - `python main.py analyze --resume resume.txt --jd jd.txt --company 字节 --role 后端`
   - 输出：
     - 匹配度分数 + 关键差距摘要。
     - 带标注的新简历文本。
     - 一份简短 markdown 报告，记录 `analysis_id` 以便回溯。


notes:

实际职场高手如何分析 JD（帮你抽成可实现逻辑）
现实中比较成熟的 JD 分析，通常分几层，不只是「提取关键词」：

1. 明面上的硬要求（显性条件）
岗位标签：职能（产品 / 开发 / 算法）、级别（P6 / 高级 / Leader）、团队类型（平台 / 业务线 / 中台）。
硬门槛：
年限（3–5 年 / 5–10 年）。
学历、专业。
必备技能：某语言、某技术栈、某行业经验。
工具/技术关键字：例如「LLM、大模型、Prompt、A/B Test、SQL、Snowflake」等。
→ 在你的工具里：

JDParser 把这些抽到：must_have_skills、experience_requirements、domain_keywords 等字段。
MatchingEngine 规则侧 先做一轮硬过滤/打分：必须要有的有没有，年限够不够。
2. 责任 & 场景：这份工作“每天在干什么”
看「岗位职责」里反复出现的动词和对象：
比如 AI PM：规划 / 方案 / 拆解 / 指标 / 实验 / 协同；
面向谁：C 端用户 / B 端客户 / 内部团队。
这是判断：
你是做「0-1 创新」还是「1-N 增长 &运营」？
偏后台平台型，还是偏前台业务 owner？
→ 在你的工具里：

responsibilities + domain_keywords：
用来匹配简历里的项目/经历：是不是写了类似「智能客服 / 智能文档 / 大模型内容生成」这些场景。
缺少对应场景，就在 gaps 里标一个「缺具体场景经验」。
3. 隐含信号：真正想要什么样的人
资深求职者会特别看这些「没有明说，但暗示很重要」的部分：

话术风格：
很多「强 owner / 结果导向 / 抗压」→ 看重自驱、抗压。
很多「跨团队 / 对齐 / 推进」→ 强调沟通协调能力。
组织位置：
「与算法、工程、运营、销售协同」→ AI 平台 / 业务支撑型角色。
「直接对接高层、客户 CXO」→ 要有高层沟通经验。
→ 在你的工具里：

这些可以由 LLM 在 soft_skills、values_keywords 里总结。
在 MatchingDimensions 里对应 leadership、communication、culture_fit，由 LLM 给出 0–1 或 0–100 的主观评分和解释。
4. 环境 & 预期：公司规模、阶段、难度
这些通常不会写在 JD 文本里，但高手会结合外部信息看：

公司规模 / 阶段：独角兽、小巨头、大厂，对「方法论 vs. 落地」要求不同。
业务阶段：
很多「从 0 到 1 / 探索」→ 容错高，但需要强创业精神。
很多「优化 / 迭代 / 提效」→ 更偏体系化、增长/运营型能力。
面试难度：
同行业/同职位的面经信息：问得是不是偏技术细、业务深、case 多。
→ 在你的工具里：

这部分正是你后续用 Tavily + 面经分析模块 做的：
根据公司名 / 岗位名搜索外部信息，抽出：轮次结构、常问问题类型、常挂点。
再给出「面试难度 & 应对策略」的建议。
在当前阶段（只做 JD & 简历对比）：
可以先在 JobProfile 中预留字段，如 company_size_hint、stage_hint（由 LLM 根据 JD 语气粗略推断，后面再用外部信息覆盖）。


对应到你项目里的分析流程（从“高手做法”到「可编码步骤」）
先不考虑 Tavily，只针对 JD → 简历分析，可以这样落地：

JDParser（LLM）

从 JD 文本抽取：
硬条件：must_have_skills、experience_requirements、degree/major。
核心职责 & 场景：responsibilities、domain_keywords。
软性信号：soft_skills、values_keywords。
可选：让 LLM 在 JSON 里顺便写一个 company_expectation_summary 段落，方便后面直接展示给用户。
ResumeParser（LLM）

从简历抽取：
项目/经历列表 + 负责内容（experiences / projects）。
技能列表（skills）。
个人总结中的关键词（owner、增长、实验、跨部门协作等）。
年限、证书、语言、行业标签等。
MatchingEngine（规则 + LLM）

规则侧先对比：
技能交集 / JD 必须技能的覆盖情况。
年限是否达标（用 years_of_experience）。
是否有同类型场景（比如智能客服 / 智能文档 / AI 产品）。
生成基准分和差距列表。
把这些 + 简历/JD 片段丢给 LLM，让它：
给各个维度（skills / experience / domain / education / soft / leadership / communication / culture_fit）打分。
在 dimension_explanations 里写几句解释。
简历重写（ResumeRewriter）

用 MatchingResult.gaps + JobProfile 去指导 LLM：
哪些内容要加强（比如项目要写出指标、场景对齐 JD 等）。
自动产出修改后的简历 + 修改点列表。