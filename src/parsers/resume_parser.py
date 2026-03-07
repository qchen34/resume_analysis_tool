from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Tuple

from pydantic import ValidationError

from src.llm.client import llm_client
from src.llm.prompts import RESUME_PARSE_SYSTEM_PROMPT
from src.models.schemas import ResumeProfile


def _strip_markdown_fences(content: str) -> str:
    """
    处理模型可能返回的 ```json ... ``` 形式，将其中的 JSON 文本提取出来。
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_year_month(value: str | None) -> date | None:
    """
    将诸如 '2015.09'、'2019-06'、'2015/09' 或 '2015年09月' 等字符串，
    粗略解析为该月的第一天，用于 Education / Experience 的 start_date / end_date。
    对于 '至今'、'现在' 等返回 None。
    """
    if not value:
        return None

    text = str(value).strip()
    if not text or any(tok in text for tok in ("至今", "现在")):
        return None

    # 统一分隔符
    text = (
        text.replace("年", ".")
        .replace("月", "")
        .replace("/", "-")
    )

    year: int | None = None
    month: int = 1

    if "." in text and "-" not in text:
        parts = text.split(".")
    else:
        parts = text.split("-")

    try:
        if len(parts) == 2:
            year = int(parts[0])
            month = int(parts[1])
        elif len(parts) == 1 and len(parts[0]) == 4:
            year = int(parts[0])
            month = 1
    except ValueError:
        return None

    if year is None:
        return None

    try:
        return date(year, month, 1)
    except ValueError:
        return None


def _map_llm_resume_to_profile_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 LLM 返回的更“自然”的简历 JSON（例如 basic_info / work_experience / project_experience 等）
    映射为符合 ResumeProfile 结构的字典。
    """
    basic: Dict[str, Any] = data.get("basic_info") or {}

    # summary 可能是字符串或列表，这里统一成 List[str]
    raw_summary = data.get("summary") or []
    if isinstance(raw_summary, str):
        summary: List[str] = [raw_summary]
    elif isinstance(raw_summary, list):
        summary = [str(item) for item in raw_summary]
    else:
        summary = []
    work_experience: List[Dict[str, Any]] = data.get("work_experience") or []
    project_experience: List[Dict[str, Any]] = data.get("project_experience") or []
    education_raw: List[Dict[str, Any]] = data.get("education") or []

    # skills 字段在不同简历中可能是 list 或 dict，这里做兼容处理
    raw_skills: Any = data.get("skills") or []
    skills_list: List[str] = []
    certificates: List[str] = data.get("certificates") or []
    languages: List[str] = data.get("languages") or []

    if isinstance(raw_skills, list):
        skills_list.extend(str(s) for s in raw_skills)
    elif isinstance(raw_skills, dict):
        # 例如 {"technical": [...], "languages": [...], "certificates": [...]}
        for key, value in raw_skills.items():
            if key == "languages":
                if isinstance(value, list):
                    languages.extend(str(v) for v in value)
                elif isinstance(value, str):
                    languages.append(value)
            elif key == "certificates":
                if isinstance(value, list):
                    certificates.extend(str(v) for v in value)
                elif isinstance(value, str):
                    certificates.append(value)
            else:
                # 其余都归为 skills others
                if isinstance(value, list):
                    skills_list.extend(str(v) for v in value)
                elif isinstance(value, str):
                    skills_list.append(value)

    experiences: List[Dict[str, Any]] = []
    # 记录最早/最晚时间点，用于估算总工作年限
    earliest_start: date | None = None
    latest_end: date | None = None

    for we in work_experience:
        start = _parse_year_month(we.get("start_date"))
        end = _parse_year_month(we.get("end_date"))

        if start:
            earliest_start = start if earliest_start is None else min(earliest_start, start)
        if end:
            latest_end = end if latest_end is None else max(latest_end, end)

        experiences.append(
            {
                "company": we.get("company", ""),
                "title": we.get("position", "") or we.get("title", ""),
                "type": None,
                "start_date": start,
                "end_date": end,
                "bullets": we.get("responsibilities") or [],
                "tech_stack": [],
            }
        )

    projects: List[Dict[str, Any]] = []
    for pr in project_experience:
        projects.append(
            {
                "name": pr.get("project_name", "") or pr.get("name", ""),
                "role": None,
                "start_date": None,
                "end_date": None,
                "bullets": pr.get("key_points") or pr.get("highlights") or [],
                "tech_stack": [],
                "impact_metric": None,
            }
        )

    educations: List[Dict[str, Any]] = []
    for ed in education_raw:
        raw_details = ed.get("details")
        if raw_details is None:
            raw_details = ed.get("highlights")
        if isinstance(raw_details, str):
            highlights: List[str] = [raw_details]
        elif isinstance(raw_details, list):
            highlights = [str(h) for h in raw_details]
        else:
            highlights = []

        educations.append(
            {
                "school": ed.get("school", ""),
                "degree": ed.get("degree"),
                "major": ed.get("major"),
                "start_date": _parse_year_month(ed.get("start_date")),
                "end_date": _parse_year_month(ed.get("end_date")),
                "gpa": None,
                "highlights": highlights,
            }
        )

    # 如果 basic_info 中没有给总年限，则根据最早/最晚时间估算一个粗略年限
    exp_years = basic.get("experience_years")
    if exp_years is None and earliest_start and latest_end and latest_end > earliest_start:
        delta_days = (latest_end - earliest_start).days
        exp_years = round(delta_days / 365.0, 1)

    profile_data: Dict[str, Any] = {
        "name": basic.get("name"),
        "headline": basic.get("title"),
        "email": basic.get("email"),
        "phone": basic.get("phone"),
        "location": basic.get("location"),
        "summary": summary,
        "links": basic.get("personal_links") or [],
        "years_of_experience": exp_years,
        "certifications": certificates,
        "spoken_languages": languages,
        "education": educations,
        "experiences": experiences,
        "projects": projects,
        "skills": {
            # 暂时将 LLM 返回的 skills 列表全部放入 others，后续可以做更细粒度分类
            "languages": [],
            "frameworks": [],
            "tools": [],
            "others": skills_list,
        },
    }
    return profile_data


def parse_resume(raw_text: str) -> Tuple[ResumeProfile, str]:
    """
    使用 LLM 将原始简历文本解析为结构化 ResumeProfile。

    返回:
        (resume_profile, raw_json_str) 其中 raw_json_str 为映射后的 Profile JSON 字符串。
    """
    user_prompt = (
        "下面是一份候选人的完整简历文本，请根据 system 提示中的字段要求，"
        "提取出结构化信息并输出为一个 JSON 对象。\n\n"
        "简历内容：\n"
        "-----\n"
        f"{raw_text}\n"
        "-----\n"
    )

    messages = [
        {"role": "system", "content": RESUME_PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw_output = llm_client.chat(messages, temperature=0.5)
    cleaned = _strip_markdown_fences(raw_output)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to decode Resume JSON from LLM output: {exc}\nOutput was:\n{raw_output}") from exc

    # 先将 LLM 的 JSON 映射为符合 ResumeProfile 结构的字典，再交给 Pydantic 做二次校验
    mapped_data = _map_llm_resume_to_profile_data(data)

    try:
        resume_profile = ResumeProfile.model_validate(mapped_data)
    except ValidationError as exc:
        raise RuntimeError(
            f"Mapped Resume JSON did not match ResumeProfile schema: {exc}\nData was:\n{mapped_data}"
        ) from exc

    # 使用 Pydantic 的 JSON 友好导出，自动处理 date 等类型
    json_ready = resume_profile.model_dump(mode="json")
    json_str = json.dumps(json_ready, ensure_ascii=False, indent=2)
    return resume_profile, json_str


