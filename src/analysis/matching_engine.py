from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Tuple
import os
from src.llm.client import llm_client
from src.llm.prompts import MATCH_SCORING_SYSTEM_PROMPT
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


def _refine_matching_fast_with_llm(
    resume: ResumeProfile,
    job: JobProfile,
    base: MatchingBaseResult,
) -> Dict[str, object]:
    """
    使用 flash/fast 模型在规则层基准结果的基础上进行微调和解释。
    返回 dict，将会被用来更新 MatchingResult（overall_score、dimension_explanations 等）。
    """
    user_prompt = (
        "下面是一个职位与候选人简历的结构化匹配基准结果，请你以资深简历顾问的视角，"
        "在不违背基础事实的前提下，适度微调各维度得分，并给出详细中文解释。\n\n"
        "基础打分结果（JSON）：\n"
        f"{base.__dict__}\n\n"
        "职位画像（JobProfile，JSON）：\n"
        f"{job.model_dump(mode='json')}\n\n"
        "简历画像（ResumeProfile，JSON）：\n"
        f"{resume.model_dump(mode='json')}\n\n"
        "dimension_explanations 写作要求：每个维度至少 2～4 句话，尽量引用简历或 JD 中的具体内容（如某条工作 bullet、技能项、年限、公司/项目名）作为依据，避免空泛。\n"
        "建议结构：① 与 JD 的对应（简历中哪些内容命中或未命中）；② 差距或亮点；③ 可选的面试/投递建议。\n\n"
        "示例（skills）：\"JD 要求 TensorFlow、分布式计算、模型训练。简历技能列表中有 PyTorch、Python，某条工作经历提到「分布式架构设计」「容器化部署」，但未直接写 TensorFlow 与模型训练。技能维度匹配度中等，建议面试时用项目细节补足。\"\n"
        "示例（experience）：\"JD 要求 5～10 年。简历推算约 4 年，略低。但候选人含华为及海外大项目（如 $58M 标案），项目复杂度高，可部分弥补年限；投递时可强调项目规模与角色深度。\"\n\n"
        "请按上述详细程度与风格，为所有维度撰写 dimension_explanations。\n\n"
        "请严格输出一个 JSON 对象，包含字段：\n"
        "{\n"
        "  \"overall_score\": number,\n"
        "  \"dimensions\": {\n"
        "    \"skills\": number,\n"
        "    \"experience\": number,\n"
        "    \"domain\": number,\n"
        "    \"education\": number,\n"
        "    \"soft_skills_signal\": number,\n"
        "    \"leadership\": number,\n"
        "    \"communication\": number,\n"
        "    \"culture_fit\": number\n"
        "  },\n"
        "  \"explanation\": string,\n"
        "  \"dimension_explanations\": {\n"
        "    \"skills\": string,\n"
        "    \"experience\": string,\n"
        "    \"domain\": string,\n"
        "    \"education\": string,\n"
        "    \"soft_skills_signal\": string,\n"
        "    \"leadership\": string,\n"
        "    \"communication\": string,\n"
        "    \"culture_fit\": string\n"
        "  },\n"
        "}\n"
        "不要输出任何除 JSON 之外的文字。"
    )

    messages = [
        {"role": "system", "content": MATCH_SCORING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = llm_client.chat(messages, temperature=0.3)

    # 去掉可能的 ```json ... ``` 包裹，并只解析第一个完整 JSON 对象
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start == -1:
        raise RuntimeError(
            f"LLM 匹配精炼返回中未找到 JSON 对象。输出片段:\n{raw[:800]}"
        )
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to decode LLM matching refinement JSON: {exc}\nOutput was:\n{raw}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError("LLM 匹配精炼返回的 JSON 根节点不是对象。")

    return data


def _analyze_responsibilities_with_reasoning_llm(
    resume: ResumeProfile,
    job: JobProfile,
) -> Dict[str, object]:
    """
    使用 reasoning 模型对 JD 岗位职责与任职要求进行更细致的语义匹配分析，
    返回一个 dict，其中包括 responsibility_coverage 和 skill_coverage。
    """
    # 优先使用 reasoning 模型；未配置时用默认模型，保证报告中有职责/任职覆盖度
    reasoning_model = os.getenv("GEMINI_REASONING_MODEL") or os.getenv("GEMINI_MODEL")
    if not reasoning_model:
        return {}

    user_prompt = (
        "下面给出一个职位的岗位职责列表（responsibilities）、任职要求（must_have_skills / nice_to_have_skills / "
        "experience_requirements），以及候选人简历的结构化信息。\n"
        "请你：\n"
        "1）逐条阅读 JD 的岗位职责，在简历中寻找最相关的 1-3 条 bullet 作为证据，并给出每条职责的匹配度；\n"
        "2）逐条阅读 JD 的任职要求（包括必须技能、加分技能和工作年限要求），在简历中寻找最相关的技能/证书/项目/经历，"
        "   给出每一条要求的匹配度。\n\n"
        "要求：\n"
        "- 匹配度 match_score 为 0 到 1 的数字，0 表示完全不匹配，1 表示高度匹配。\n"
        "- evidence_bullets 可以来自简历的工作经历 bullets、项目 bullets、个人总结或技能/证书描述中的关键句。\n"
        "- 如果暂时找不到明显证据，可以将 match_score 设为 0 或较低，并给出空列表或简短说明。\n\n"
        "职位岗位职责（JobProfile.responsibilities，JSON 数组）：\n"
        f"{job.responsibilities}\n\n"
        "职位任职要求（must_have_skills / nice_to_have_skills / experience_requirements）：\n"
        f"must_have_skills: {job.must_have_skills}\n"
        f"nice_to_have_skills: {job.nice_to_have_skills}\n"
        f"experience_requirements: {job.experience_requirements}\n\n"
        "候选人简历（ResumeProfile，JSON）：\n"
        f"{resume.model_dump(mode='json')}\n\n"
        "请严格输出一个 JSON 对象，包含字段：\n"
        "{\n"
        "  \"responsibility_coverage\": [\n"
        "    {\n"
        "      \"jd_item\": string,\n"
        "      \"match_score\": number,\n"
        "      \"evidence_bullets\": list[string]\n"
        "    }\n"
        "  ],\n"
        "  \"skill_coverage\": [\n"
        "    {\n"
        "      \"jd_requirement\": string,      # JD 中的一条任职要求原文或精简版\n"
        "      \"requirement_type\": string,    # \"must\" | \"nice\" | \"experience\"\n"
        "      \"match_score\": number,\n"
        "      \"evidence_bullets\": list[string]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "不要输出任何除 JSON 之外的文字。"
    )

    messages = [
        {"role": "system", "content": MATCH_SCORING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = llm_client.chat(messages, model=reasoning_model, temperature=0)

    # 去掉可能的 ```json ... ``` 包裹，并只解析第一个完整 JSON 对象（避免尾部多余输出）
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start == -1:
        raise RuntimeError(
            f"Reasoning LLM 返回中未找到 JSON 对象。输出片段:\n{raw[:800]}"
        )
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to decode reasoning LLM responsibility coverage JSON: {exc}\nOutput was:\n{raw}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError("Reasoning LLM 返回的 JSON 根节点不是对象。")

    return data


def compute_matching(resume: ResumeProfile, job: JobProfile) -> MatchingResult:
    """
    规则 + LLM 结合的匹配度计算主入口。
    先用规则层打基准分，再用 LLM 做精炼解释。
    """
    base = _compute_base_matching(resume, job)

    # 先构建一个带有规则层结果的 MatchingResult，LLM 只负责补充/微调
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

    # 调用 flash 模型进行精炼
    refined = _refine_matching_fast_with_llm(resume, job, base)

    overall_score = float(refined.get("overall_score", base.overall_score_raw * 100))
    dim_updates: Dict[str, float] = refined.get("dimensions", {}) or {}

    # 更新维度分数（如果 LLM 返回了新的值）
    for field in ("skills", "experience", "domain", "education", "soft_skills_signal", "leadership", "communication", "culture_fit"):
        if field in dim_updates:
            setattr(dimensions, field, float(dim_updates[field]))

    explanation = refined.get("explanation")
    dimension_explanations = refined.get("dimension_explanations") or {}

    return MatchingResult(
        overall_score_raw=base.overall_score_raw,
        overall_score=overall_score,
        dimensions=dimensions,
        gaps=base.gaps,
        explanation=explanation,
        dimension_explanations=dimension_explanations,
    )


def compute_matching_with_details(
    resume: ResumeProfile,
    job: JobProfile,
) -> Tuple[MatchingResult, Dict[str, object]]:
    """
    与 compute_matching 类似，但额外返回 LLM 精炼阶段的原始 JSON dict，
    其中包括 responsibility_coverage 等调试信息。
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

    # 1) 使用 flash 模型做基础精炼（得分 + 维度 + 总体解释）
    refined_fast = _refine_matching_fast_with_llm(resume, job, base)

    # 2) 使用 reasoning 模型做更细致的职责/任职要求覆盖分析（可能比较耗时/耗费 token）
    refined_reasoning = _analyze_responsibilities_with_reasoning_llm(resume, job)

    # 合并两个结果：fast 为主，reasoning 提供补充字段（如 responsibility_coverage）
    refined: Dict[str, object] = {}
    refined.update(refined_fast)
    # 对于 reasoning 结果，优先合并不会与 fast 冲突的新字段
    for k, v in refined_reasoning.items():
        if k not in refined:
            refined[k] = v

    overall_score = float(refined.get("overall_score", base.overall_score_raw * 100))
    dim_updates: Dict[str, float] = refined.get("dimensions", {}) or {}

    for field in (
        "skills",
        "experience",
        "domain",
        "education",
        "soft_skills_signal",
        "leadership",
        "communication",
        "culture_fit",
    ):
        if field in dim_updates:
            setattr(dimensions, field, float(dim_updates[field]))

    explanation = refined.get("explanation")
    dimension_explanations = refined.get("dimension_explanations") or {}

    resp_cov = refined_reasoning.get("responsibility_coverage") or []
    skill_cov = refined_reasoning.get("skill_coverage") or []
    gaps: List[GapItem] = list(base.gaps)

    # 4.1 用 skill_coverage 微调 skills/experience 维度分，并减轻“语义上已部分满足”的技能差距
    if skill_cov:
        must_scores: List[float] = []
        exp_scores: List[float] = []
        for item in skill_cov:
            try:
                ms = float(item.get("match_score", 0) or 0)
            except (TypeError, ValueError):
                ms = 0.0
            rtype = (str(item.get("requirement_type") or "")).lower()
            if rtype == "must":
                must_scores.append(ms)
            elif rtype == "experience":
                exp_scores.append(ms)

        if must_scores:
            avg_must = sum(must_scores) / len(must_scores)
            dimensions.skills = max(0.0, min(1.0, 0.5 * dimensions.skills + 0.5 * avg_must))
        if exp_scores:
            avg_exp = sum(exp_scores) / len(exp_scores)
            dimensions.experience = max(
                0.0, min(1.0, 0.7 * dimensions.experience + 0.3 * avg_exp)
            )

        for gap in gaps:
            if gap.type != "skill" or not gap.name:
                continue
            for item in skill_cov:
                req_text = str(item.get("jd_requirement") or "")
                try:
                    ms = float(item.get("match_score", 0) or 0)
                except (TypeError, ValueError):
                    ms = 0.0
                if gap.name in req_text and ms >= 0.6:
                    if gap.severity == "high":
                        gap.severity = "medium"
                    elif gap.severity == "medium":
                        gap.severity = "low"
                    hint = (
                        "【语义匹配提示】虽未直接写出该技能，但简历中已有相关证据（如证书、项目），优先级已下调。"
                    )
                    gap.detail = (gap.detail or "") + ("\n" + hint if gap.detail else hint)
                    break

    # 4.2 用职责/任职覆盖度补充维度解释（文字）
    if resp_cov:
        lines: List[str] = []
        for item in resp_cov[:5]:
            jd_item = item.get("jd_item", "")
            try:
                ms = float(item.get("match_score", 0) or 0)
            except (TypeError, ValueError):
                ms = 0.0
            ev = item.get("evidence_bullets") or []
            ev_preview = "；".join(ev[:2])
            lines.append(f"- 职责「{jd_item}」匹配度约为 {ms:.2f}，证据：{ev_preview}")
        extra = "按 JD 岗位职责逐条语义匹配的结果示例：\n" + "\n".join(lines)
        prev = dimension_explanations.get("experience") or ""
        dimension_explanations["experience"] = (prev + "\n" if prev else "") + extra

    if skill_cov:
        lines = []
        for item in skill_cov[:6]:
            req = item.get("jd_requirement", "")
            rtype = str(item.get("requirement_type") or "")
            try:
                ms = float(item.get("match_score", 0) or 0)
            except (TypeError, ValueError):
                ms = 0.0
            ev = item.get("evidence_bullets") or []
            ev_preview = "；".join(ev[:2])
            lines.append(
                f"- 要求（{rtype}）「{req}」匹配度约为 {ms:.2f}，简历中的相关证据：{ev_preview}"
            )
        extra = "按 JD 任职要求逐条语义匹配的结果示例：\n" + "\n".join(lines)
        prev = dimension_explanations.get("skills") or ""
        dimension_explanations["skills"] = (prev + "\n" if prev else "") + extra

    result = MatchingResult(
        overall_score_raw=base.overall_score_raw,
        overall_score=overall_score,
        dimensions=dimensions,
        gaps=gaps,
        explanation=explanation,
        dimension_explanations=dimension_explanations,
    )

    return result, refined

