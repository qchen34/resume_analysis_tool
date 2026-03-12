from __future__ import annotations

import json
from typing import Tuple

from src.models.schemas import ResumeProfile
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


