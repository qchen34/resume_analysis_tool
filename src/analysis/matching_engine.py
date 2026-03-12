from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Tuple
import os

from src.llm.client import llm_client
from src.models.schemas import (
    GapItem,
    JobProfile,
    MatchingDimensions,
    MatchingResult,
    ResumeProfile,
)


@dataclass
class MatchingBaseResult:
    """规则层基准打分的中间结果结构。"""

    overall_score_raw: float
    dimensions: Dict[str, float]
    gaps: List[GapItem]


def _compute_skill_score(resume: ResumeProfile, job: JobProfile) -> Tuple[float, List[GapItem]]:
    resume_skill_set = set(
        (resume.skills.languages or [])
        + (resume.skills.frameworks or [])
        + (resume.skills.tools or [])
        + (resume.skills.others or [])
    )
    must = set(job.must_have_skills or [])
    nice = set(job.nice_to_have_skills or [])

    gaps: List[GapItem] = []
    if not must:
        return 1.0, gaps

    hit_must = must & resume_skill_set
    miss_must = must - resume_skill_set
    hit_nice = nice & resume_skill_set

    base = len(hit_must) / len(must)
    bonus = 0.1 * min(1.0, len(hit_nice) / max(1, len(nice))) if nice else 0.0
    score = min(1.0, base + bonus)

    for s in sorted(miss_must):
        gaps.append(
            GapItem(
                type="skill",
                name=s,
                severity="high",
                detail=f"JD 要求的核心技能「{s}」在简历中未明确体现，可作为后续补充方向，不建议在简历中捏造。",
            )
        )

    return score, gaps


def _compute_experience_score(resume: ResumeProfile, job: JobProfile) -> Tuple[float, List[GapItem]]:
    """
    基于总年限 + 领域命中情况的经验维度打分，并在年限明显不足时生成经验类差距。
    """
    gaps: List[GapItem] = []
    score = 0.5
    years = resume.years_of_experience or 0.0
    req = job.experience_requirements or ""

    required_min: float | None = None
    required_desc = req.strip() or "未在 JD 中明确写出"

    if "3-6" in req or "3–6" in req:
        required_min = 3.0
        if years >= 3:
            score += 0.3
        if years >= 5:
            score += 0.2
    elif "5-10" in req or "5–10" in req:
        required_min = 5.0
        if years >= 5:
            score += 0.3
        if years >= 7:
            score += 0.2

    # 年限明显低于要求时，记录为高优先级差距（但不一票否决）
    if required_min is not None and years < required_min:
        gaps.append(
            GapItem(
                type="experience",
                name="工作年限可能不足",
                severity="high" if required_min - years >= 1 else "medium",
                detail=(
                    f"JD 对工作年限的要求为「{required_desc}」，当前简历解析出的总年限约为 {years} 年，"
                    "建议在实际投递前结合自身真实经历谨慎评估是否符合要求。"
                ),
            )
        )

    # 简单看是否有与 JD 领域关键字直接相关的项目/经历
    domain_hits = 0
    for exp in resume.experiences:
        text = " ".join(exp.bullets or [])
        for kw in job.domain_keywords or []:
            if kw and kw in text:
                domain_hits += 1
                break
    if domain_hits > 0:
        score += 0.1

    score = max(0.0, min(1.0, score))
    return score, gaps


def _compute_domain_score(resume: ResumeProfile, job: JobProfile) -> float:
    if not job.domain_keywords:
        return 1.0

    resume_texts: List[str] = []
    for exp in resume.experiences:
        resume_texts.extend(exp.bullets or [])
    for proj in resume.projects:
        resume_texts.extend(proj.bullets or [])
    resume_blob = " ".join(resume_texts)

    hits = 0
    for kw in job.domain_keywords:
        if kw and kw in resume_blob:
            hits += 1

    return hits / len(job.domain_keywords)


def _compute_education_score(resume: ResumeProfile, job: JobProfile) -> float:
    if not resume.education:
        return 0.0
    # 简单规则：有一条教育经历即可给中等分，后续可扩展专业/学历判断
    return 0.7


def _compute_soft_scores(resume: ResumeProfile, job: JobProfile) -> Tuple[float, float, float, float]:
    """
    基于软技能关键词的一个非常粗糙的规则打分，后续主要由 LLM 精炼。
    返回: (soft_skills_signal, leadership, communication, culture_fit)
    """
    text_pieces: List[str] = []
    text_pieces.extend(resume.summary or [])
    for exp in resume.experiences:
        text_pieces.extend(exp.bullets or [])
    blob = " ".join(text_pieces)

    def has_any(keywords: List[str]) -> bool:
        return any(kw for kw in keywords if kw and kw in blob)

    leadership_kw = ["主导", "牵头", "owner", "负责整体", "端到端"]
    communication_kw = ["跨团队", "协调", "对齐", "沟通", "对接"]
    culture_kw = ["长期主义", "客户导向", "数据驱动", "复盘", "持续优化", "主人翁"]

    soft_base = 0.5
    leadership = 0.7 if has_any(leadership_kw) else 0.3
    communication = 0.7 if has_any(communication_kw) else 0.3
    culture_fit = 0.6 if has_any(culture_kw) else 0.4

    soft_signal = (leadership + communication + culture_fit) / 3
    soft_signal = (soft_signal + soft_base) / 2

    return soft_signal, leadership, communication, culture_fit


def _compute_base_matching(resume: ResumeProfile, job: JobProfile) -> MatchingBaseResult:
    skill_score, skill_gaps = _compute_skill_score(resume, job)
    experience_score, experience_gaps = _compute_experience_score(resume, job)
    domain_score = _compute_domain_score(resume, job)
    education_score = _compute_education_score(resume, job)
    soft_signal, leadership, communication, culture_fit = _compute_soft_scores(resume, job)

    # 维度权重可以后续调整，这里先做一个简单加权
    weights = {
        "skills": 0.3,
        "experience": 0.25,
        "domain": 0.15,
        "education": 0.1,
        "soft_skills_signal": 0.2,
    }
    dim = {
        "skills": skill_score,
        "experience": experience_score,
        "domain": domain_score,
        "education": education_score,
        "soft_skills_signal": soft_signal,
    }
    overall_raw = sum(dim[k] * w for k, w in weights.items())

    dimensions = MatchingDimensions(
        skills=skill_score,
        experience=experience_score,
        domain=domain_score,
        education=education_score,
        soft_skills_signal=soft_signal,
        leadership=leadership,
        communication=communication,
        culture_fit=culture_fit,
    )

    gaps: List[GapItem] = []
    gaps.extend(skill_gaps)
    gaps.extend(experience_gaps)

    return MatchingBaseResult(
        overall_score_raw=overall_raw,
        dimensions={
            "skills": skill_score,
            "experience": experience_score,
            "domain": domain_score,
            "education": education_score,
            "soft_skills_signal": soft_signal,
            "leadership": leadership,
            "communication": communication,
            "culture_fit": culture_fit,
        },
        gaps=gaps,
    )


def _compute_semantic_alignment(
    resume: ResumeProfile,
    job: JobProfile,
    jd_text: str | None = None,
    resume_text: str | None = None,
) -> Dict[str, object]:
    """
    使用 fast 模型做一句话级别的语义对齐，只生成“证据对”，不做评分。

    输出结构示例：
    {
      "responsibility_semantic_matches": [
        { "jd_item": "...", "resume_sentences": ["...", "..."] },
        ...
      ],
      "requirement_semantic_matches": [
        { "jd_requirement": "...", "resume_sentences": ["...", "..."] },
        ...
      ]
    }
    """
    model = os.getenv("GEMINI_MODEL")
    if not model:
        return {}

    # 收集简历中的句子/要点：
    # 1）优先使用结构化 ResumeProfile 中的 summary / bullets；
    # 2）如果解析层未填充任何内容，则退回到 resume_text 的逐行拆分。
    resume_sentences: List[str] = []
    resume_sentences.extend(resume.summary or [])
    for exp in resume.experiences:
        resume_sentences.extend(exp.bullets or [])
    for proj in resume.projects:
        resume_sentences.extend(proj.bullets or [])

    if not resume_sentences and resume_text:
        for line in resume_text.splitlines():
            line = line.strip()
            if line:
                resume_sentences.append(line)

    # JD 职责与要求列表（尽量保留 JD 原句）
    jd_resps: List[str] = []
    jd_reqs: List[str] = []

    if jd_text:
        raw_lines = [ln.strip() for ln in jd_text.splitlines()]
        section: str | None = None
        for line in raw_lines:
            if not line:
                continue
            if line.startswith("岗位职责"):
                section = "resp"
                continue
            if line.startswith("任职要求"):
                section = "req"
                continue
            if line.startswith("加分项"):
                section = "req"
                jd_reqs.append(line)
                continue
            if section == "resp":
                jd_resps.append(line)
            elif section == "req":
                jd_reqs.append(line)

    # 如果从原文中没抽到，则退回到 JobProfile 中的结构化字段
    if not jd_resps:
        jd_resps = job.responsibilities or []
    if not jd_reqs:
        jd_reqs.extend(job.must_have_skills or [])
        jd_reqs.extend(job.nice_to_have_skills or [])
        if job.experience_requirements:
            jd_reqs.append(job.experience_requirements)

    if not jd_resps and not jd_reqs:
        return {}
    if not resume_sentences:
        return {}

    system_prompt = (
        "你是一个只做“句子级语义对齐”的助手，不做任何打分或评价。\n"
        "你会得到：\n"
        "1）JD 职责列表（responsibilities，中文句子数组）；\n"
        "2）JD 任职要求列表（requirements，技能或要求的句子/短语数组）；\n"
        "3）简历句子/要点列表（resume_sentences，来自简历的 bullet 或完整句子）。\n\n"
        "任务：\n"
        "- 对于 responsibilities 中的每一条 jd_item，在 resume_sentences 中选择最相关的 0～3 条句子，"
        "  用原文返回到 resume_sentences 字段；\n"
        "- 对于 requirements 中的每一条 jd_requirement，同样在 resume_sentences 中选择最相关的 0～3 条句子；\n"
        "- 如果找不到合适的句子，可以返回空列表。\n\n"
        "重要：\n"
        "- 不要输出任何评分（例如 match_score）、评论或解释；\n"
        "- 只做“句子配对”，严格按指定 JSON 结构输出结果。"
    )

    user_prompt = (
        "JD 职责列表（responsibilities，JSON 数组）：\n"
        f"{json.dumps(jd_resps, ensure_ascii=False)}\n\n"
        "JD 任职要求列表（requirements，JSON 数组）：\n"
        f"{json.dumps(jd_reqs, ensure_ascii=False)}\n\n"
        "简历句子/要点列表（resume_sentences，JSON 数组）：\n"
        f"{json.dumps(resume_sentences, ensure_ascii=False)}\n\n"
        "请严格按照下面的 JSON 结构输出（不要多任何其它文字）：\n"
        "{\n"
        "  \"responsibility_semantic_matches\": [\n"
        "    { \"jd_item\": string, \"resume_sentences\": list[string] }\n"
        "  ],\n"
        "  \"requirement_semantic_matches\": [\n"
        "    { \"jd_requirement\": string, \"resume_sentences\": list[string] }\n"
        "  ]\n"
        "}\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    raw = llm_client.chat(messages, model=model, temperature=0.1)

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}

    if not isinstance(data, dict):
        return {}

    # 只保留预期字段，防止模型乱输出其它内容
    result: Dict[str, object] = {}
    if isinstance(data.get("responsibility_semantic_matches"), list):
        result["responsibility_semantic_matches"] = data["responsibility_semantic_matches"]
    if isinstance(data.get("requirement_semantic_matches"), list):
        result["requirement_semantic_matches"] = data["requirement_semantic_matches"]
    return result


def compute_matching(resume: ResumeProfile, job: JobProfile) -> MatchingResult:
    """
    纯规则层匹配：只根据规则打分，不再调用 LLM 做精炼。
    """
    base = _compute_base_matching(resume, job)

    dimensions = MatchingDimensions(
        skills=base.dimensions["skills"],
        experience=base.dimensions["experience"],
        domain=base.dimensions["domain"],
        education=base.dimensions["education"],
        soft_skills_signal=base.dimensions["soft_skills_signal"],
        leadership=base.dimensions["leadership"],
        communication=base.dimensions["communication"],
        culture_fit=base.dimensions["culture_fit"],
    )

    overall_score = base.overall_score_raw * 100.0

    return MatchingResult(
        overall_score_raw=base.overall_score_raw,
        overall_score=overall_score,
        dimensions=dimensions,
        gaps=base.gaps,
        explanation=None,
        dimension_explanations=None,
    )


def compute_matching_with_details(
    resume: ResumeProfile,
    job: JobProfile,
    jd_text: str | None = None,
    resume_text: str | None = None,
) -> Tuple[MatchingResult, Dict[str, object]]:
    """
    返回规则层的 MatchingResult，以及“句子级语义对齐”的 details：
    - responsibility_semantic_matches：每条 JD 职责对应的简历句子列表；
    - requirement_semantic_matches：每条 JD 任职要求对应的简历句子列表。
    不再在此处做评分，后续由上层“大牛辩论”等节点使用这些对齐结果自行打分。
    """
    result = compute_matching(resume, job)
    details = _compute_semantic_alignment(resume, job, jd_text=jd_text, resume_text=resume_text)
    return result, details

