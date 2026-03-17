"""
基于 Tavily 搜索结果，用 Fast 模型做公司画像总结（规模、行业梯队、名声、上市信息等）。
"""
from __future__ import annotations

from typing import Any, Dict

from src.llm.client import llm_client


def summarize_company_from_tavily(insights: Dict[str, Any]) -> str:
    """
    根据 Tavily 返回的 search_results 与元信息，用当前 GEMINI_MODEL 生成一段公司画像总结。
    涵盖：公司规模（人数/大厂/独角兽/初创）、行业梯队、名声口碑、若为上市公司则补充公开信息。
    """
    company = (insights.get("company") or "").strip()
    role = (insights.get("role_title") or "").strip()
    results = insights.get("search_results") or []

    chunks: list[str] = []
    for idx, r in enumerate(results, 1):
        r_type = r.get("type", "")
        summary = (r.get("summary") or "").strip()
        if not summary or summary.startswith("(摘要未包含"):
            continue
        links = r.get("links") or []
        link_titles = "；".join((lk.get("title") or lk.get("url") or "") for lk in links[:3])
        chunks.append(f"[{idx}] 类型={r_type}\n摘要：{summary}\n链接标题：{link_titles}")

    context = "\n\n".join(chunks) if chunks else "（暂无有效摘要，仅有查询计划或未启用 Tavily。）"

    system_prompt = (
        "你是一名职业规划与行业研究顾问，需要基于若干条搜索摘要，对一家公司做一个简短的画像总结。\n"
        "请尽量从摘要中提炼并分点说明：\n"
        "1）公司规模：大致人数、是否大厂/独角兽/初创、融资阶段等；\n"
        "2）行业与梯队：所在行业、主要玩家、该公司在行业中的大致位置（头部/腰部/长尾）；\n"
        "3）名声与口碑：市场评价、是否频繁裁员、加班文化、稳定性等（仅基于摘要，不编造）；\n"
        "4）若为上市公司：可补充市值区间、主营业务、近期是否有明显负面或利好新闻。\n"
        "回答用中文，分点简洁；若摘要中缺乏某类信息，可注明「摘要中未提及」。"
    )
    user_prompt = (
        f"公司：{company or '（JD 未给出公司名）'}；岗位：{role or '未指定'}。\n\n"
        "下面是 Tavily 的搜索摘要与链接标题：\n\n"
        f"{context}\n\n"
        "请按上述要求输出公司画像总结。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw = llm_client.chat(messages, temperature=0.2)
    return (raw or "").strip()
