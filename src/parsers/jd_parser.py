from __future__ import annotations

import json
from typing import Tuple

from pydantic import ValidationError

from src.llm.client import llm_client
from src.llm.prompts import JD_PARSE_SYSTEM_PROMPT
from src.models.schemas import JobProfile


def _strip_markdown_fences(content: str) -> str:
    """
    处理模型可能返回的 ```json ... ``` 形式，将其中的 JSON 文本提取出来。
    """
    text = content.strip()
    if text.startswith("```"):
        # 可能是 ```json 或 ``` 开头
        lines = text.splitlines()
        # 去掉第一行 ```xxx
        lines = lines[1:]
        # 去掉最后一行 ```（如果存在）
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_jd(raw_text: str) -> Tuple[JobProfile, str]:
    """
    使用 LLM 将原始 JD 文本解析为结构化 JobProfile。

    返回:
        (job_profile, raw_json_str)
    """
    user_prompt = (
        "Below is a job description (JD). Extract its core information into the JSON "
        "schema described in the system prompt.\n\n"
        "JD:\n"
        "-----\n"
        f"{raw_text}\n"
        "-----\n"
    )

    messages = [
        {"role": "system", "content": JD_PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw_output = llm_client.chat(messages, temperature=0.5)
    cleaned = _strip_markdown_fences(raw_output)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to decode JD JSON from LLM output: {exc}\nOutput was:\n{raw_output}") from exc

    try:
        job_profile = JobProfile.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"LLM JD JSON did not match JobProfile schema: {exc}\nData was:\n{data}") from exc

    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    return job_profile, json_str

