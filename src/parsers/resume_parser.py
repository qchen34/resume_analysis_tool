from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from src.models.schemas import Education, Experience, Project, ResumeProfile, Skills


def _strip_json_block(text: str) -> str:
    """去掉 LLM 可能返回的 ```json ... ``` 包裹。"""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _llm_resume_to_profile(data: Dict[str, Any]) -> ResumeProfile:
    """将 LLM 返回的简历 JSON 映射为 ResumeProfile。"""
    profile = ResumeProfile()
    if not isinstance(data, dict):
        return profile

    basic = data.get("basic_info") or {}
    if isinstance(basic, dict):
        profile.name = basic.get("name") or None
        profile.headline = basic.get("title") or None
        profile.email = basic.get("email") or None
        profile.phone = basic.get("phone") or None
        profile.location = basic.get("location") or None
        profile.years_of_experience = basic.get("experience_years")
        if basic.get("personal_links") and isinstance(basic["personal_links"], list):
            profile.links = [str(x) for x in basic["personal_links"]]

    if isinstance(data.get("summary"), list):
        profile.summary = [str(s) for s in data["summary"]]

    for item in (data.get("work_experience") or []):
        if not isinstance(item, dict):
            continue
        company = item.get("company") or ""
        position = item.get("position") or ""
        responsibilities = item.get("responsibilities")
        bullets = [str(b) for b in responsibilities] if isinstance(responsibilities, list) else []
        profile.experiences.append(
            Experience(company=company, title=position, bullets=bullets)
        )

    for item in (data.get("project_experience") or []):
        if not isinstance(item, dict):
            continue
        name = item.get("project_name") or ""
        key_points = item.get("key_points")
        bullets = [str(b) for b in key_points] if isinstance(key_points, list) else []
        profile.projects.append(Project(name=name, bullets=bullets))

    for item in (data.get("education") or []):
        if not isinstance(item, dict):
            continue
        school = item.get("school") or ""
        details = item.get("details")
        if isinstance(details, list):
            highlights = [str(d) for d in details]
        elif isinstance(details, str):
            highlights = [details]
        else:
            highlights = []
        profile.education.append(
            Education(school=school, degree=item.get("degree"), major=item.get("major"), highlights=highlights)
        )

    raw_skills = data.get("skills")
    if isinstance(raw_skills, list):
        profile.skills = Skills(others=[str(s) for s in raw_skills])
    elif isinstance(raw_skills, dict):
        profile.skills = Skills(
            languages=raw_skills.get("languages") or [],
            frameworks=raw_skills.get("frameworks") or [],
            tools=raw_skills.get("tools") or [],
            others=raw_skills.get("others") or [],
        )
    else:
        profile.skills = Skills()

    if isinstance(data.get("certificates"), list):
        profile.certifications = [str(c) for c in data["certificates"]]
    if isinstance(data.get("languages"), list):
        profile.spoken_languages = [str(l) for l in data["languages"]]

    return profile


def parse_resume_with_llm(raw_text: str) -> Tuple[ResumeProfile, str]:
    """
    使用 LLM 将简历原文解析为结构化 ResumeProfile，供匹配与报告使用。
    返回 (resume_profile, json_str)。
    """
    from src.llm.client import llm_client
    from src.llm.prompts import RESUME_PARSE_SYSTEM_PROMPT

    if not (raw_text or raw_text.strip()):
        return ResumeProfile(), json.dumps({"raw_text": "", "parsed": {}}, ensure_ascii=False, indent=2)

    messages = [
        {"role": "system", "content": RESUME_PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": f"请将以下简历文本解析为结构化 JSON：\n\n{raw_text.strip()}"},
    ]
    response = llm_client.chat(messages)
    cleaned = _strip_json_block(response)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    profile = _llm_resume_to_profile(data)
    json_str = json.dumps(
        {"raw_text": raw_text[:500] + ("..." if len(raw_text) > 500 else ""), "parsed": profile.model_dump(mode="json")},
        ensure_ascii=False,
        indent=2,
    )
    return profile, json_str


def parse_resume(raw_text: str) -> Tuple[ResumeProfile, str]:
    """
    简历解析（规则占位版）：当前不调用 LLM，仅返回一个空的结构化 Profile，
    并在 json_str 中保存原始简历文本，供缓存和调试使用。

    返回:
        (resume_profile, raw_json_str)
    """
    resume_profile = ResumeProfile()
    json_str = json.dumps(
        {
            "raw_text": raw_text,
            "parsed": resume_profile.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
    )
    return resume_profile, json_str


