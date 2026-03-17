from __future__ import annotations

import json
import re
from typing import Tuple

from src.models.schemas import JobProfile


def _strip_json_block(text: str) -> str:
    """去掉 LLM 可能返回的 ```json ... ``` 包裹。"""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def parse_jd_with_llm(raw_text: str) -> Tuple[JobProfile, str]:
    """
    使用 LLM 将 JD 原文解析为结构化 JobProfile，保证公司名、岗位等字段准确，供 Tavily 等下游使用。
    返回 (job_profile, json_str)，json_str 用于报告与缓存。
    """
    from src.llm.client import llm_client
    from src.llm.prompts import JD_PARSE_SYSTEM_PROMPT

    if not (raw_text or raw_text.strip()):
        return JobProfile(), json.dumps({"raw_text": "", "parsed": {}}, ensure_ascii=False, indent=2)

    messages = [
        {"role": "system", "content": JD_PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": f"请将以下 JD 文本解析为结构化 JSON：\n\n{raw_text.strip()}"},
    ]
    response = llm_client.chat(messages)
    cleaned = _strip_json_block(response)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    # 只保留 JobProfile 支持的字段，避免 Pydantic 报错
    allowed = {
        "role_title", "company", "level", "department", "location",
        "must_have_skills", "nice_to_have_skills", "responsibilities",
        "domain_keywords", "soft_skills", "values_keywords", "experience_requirements",
    }
    filtered = {k: v for k, v in data.items() if k in allowed}
    job_profile = JobProfile.model_validate(filtered)
    json_str = json.dumps(
        {"raw_text": raw_text[:500] + ("..." if len(raw_text) > 500 else ""), "parsed": job_profile.model_dump(mode="json")},
        ensure_ascii=False,
        indent=2,
    )
    return job_profile, json_str


def parse_jd(raw_text: str) -> Tuple[JobProfile, str]:
    """
    规则解析 JD：不再调用 LLM，仅做非常轻量的字段抽取，供后续模块使用。

    返回:
        (job_profile, json_str)
        其中 json_str 主要用于缓存和调试，报告中会直接展示 JD 原文。
    """
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    company: str | None = None
    role_title: str | None = None
    location: str | None = None

    if lines:
        company = lines[0]

    for line in lines:
        if "职位" in line and "：" in line:
            # 例如：职位： 大装置-售前解决方案经理（深圳）
            try:
                role_title = line.split("：", 1)[1].strip()
            except IndexError:
                role_title = None
            break

    for line in lines:
        if "薪资" in line and "：" in line:
            # 例如：薪资/经验/学历： 深圳 / 28–35K / 5-10年 / 本科
            try:
                right = line.split("：", 1)[1]
                location = right.split("/")[0].strip()
            except Exception:  # noqa: BLE001
                location = None
            break

    job_profile = JobProfile(
        role_title=role_title,
        company=company,
        location=location,
    )

    json_str = json.dumps(
        {
            "raw_text": raw_text,
            "parsed": job_profile.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
    )
    return job_profile, json_str


