"""
基于投递统计生成「大牛策略评估」：输入聚合后的投递表现文案，调用 LLM 输出策略建议。
与辩论模块解耦，不进入 LangGraph pipeline。
"""
from __future__ import annotations

from src.llm.client import llm_client
from src.llm.prompts import STRATEGY_SUMMARY_SYSTEM_PROMPT


def run_strategy_summary(
    stats_text: str,
    history_summary: str | None = None,
) -> str:
    """
    根据投递统计文案（及可选的历史分析结论摘要）生成策略评估与优化建议。

    :param stats_text: 聚合后的投递表现，如「本周投递 10 家、2 家回复、1 家面试」
    :param history_summary: 可选，历史分析结论或候选人匹配情况摘要
    :return: LLM 输出的策略建议正文
    """
    user_content = f"【投递数据统计】\n{stats_text}\n\n"
    if history_summary:
        user_content += f"【历史分析/匹配结论摘要（供参考）】\n{history_summary}\n\n"
    user_content += "请基于以上数据给出策略评估与优化建议。"

    messages = [
        {"role": "system", "content": STRATEGY_SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    raw = llm_client.chat(messages, temperature=0.4)
    return (raw or "").strip()
