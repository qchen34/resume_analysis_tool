from __future__ import annotations

from typing import Any, Dict, List, Optional

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from dataclasses import dataclass


load_dotenv()


@dataclass
class TokenUsage:
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]


class LLMClient:
    """Gemini API 封装，兼容现有的 chat(messages) 接口。

    支持通过参数选择不同模型：
    - 默认使用 GEMINI_MODEL（推荐 flash/fast 系列）。
    - 也可以显式传入 model 名称（例如 reasoning 系列模型）。
    """

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Please configure it in your .env file.")
        self._client = genai.Client(api_key=api_key)
        self._default_model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
        self.last_usage = None  # 保存最近一次调用的 token 使用情况

    def _messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
        """
        将 OpenAI 风格的 messages 列表简单串联为一个长 prompt。
        这样可以在不改动上层调用代码的前提下切换到 Gemini。
        """
        parts: List[str] = []
        for m in messages:
            role = m.get("role", "user").upper()
            content = m.get("content", "")
            parts.append(f"{role}:\n{content}\n")
        return "\n".join(parts).strip()

    def chat(self, messages: List[Dict[str, str]], model: str | None = None, **kwargs: Any) -> str:
        prompt = self._messages_to_prompt(messages)
        # 将上层传入的 temperature 等参数映射到 Gemini 的 GenerateContentConfig
        temperature = kwargs.pop("temperature", None)
        config: types.GenerateContentConfig | None = None
        if temperature is not None:
            try:
                temp_value = float(temperature)
            except (TypeError, ValueError):
                temp_value = 0.0
            config = types.GenerateContentConfig(temperature=temp_value)

        model_name = model or self._default_model

        response = self._client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config,
        )

        # 记录最近一次调用的 token 使用情况，便于外部统计
        usage_raw = getattr(response, "usage", None)
        input_tokens: Optional[int] = getattr(usage_raw, "input_tokens", None) if usage_raw else None
        output_tokens: Optional[int] = getattr(usage_raw, "output_tokens", None) if usage_raw else None

        # 如果服务端未返回 usage 信息，尝试调用 count_tokens 作为近似统计
        if input_tokens is None and output_tokens is None:
            try:
                ct = self._client.models.count_tokens(model=model_name, contents=prompt)
                # 不同版本 SDK 的字段可能略有差异，这里尽量兼容几种常见写法
                ct_usage = getattr(ct, "usage", None)
                if ct_usage is not None:
                    input_tokens = getattr(ct_usage, "input_tokens", None)
                    output_tokens = getattr(ct_usage, "output_tokens", None)
                else:
                    input_tokens = getattr(ct, "total_tokens", None)
            except Exception:
                # 统计失败时不阻塞主流程，保持 None
                pass

        total_tokens: Optional[int] = None
        if input_tokens is not None or output_tokens is not None:
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

        self.last_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        # google-genai 的 response.text 聚合了主要文本输出
        return response.text or ""


llm_client = LLMClient()

