from __future__ import annotations

import json
from typing import Tuple

from src.models.schemas import JobProfile


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


