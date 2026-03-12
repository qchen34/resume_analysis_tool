from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict
import json
import os

from langgraph.graph import StateGraph, END

from src.llm.client import llm_client
from src.parsers.jd_parser import parse_jd
from src.parsers.resume_parser import parse_resume
from src.analysis.matching_engine import compute_matching_with_details
from src.analysis.tavily_search import run_tavily_search
from src.models.schemas import JobProfile, ResumeProfile, MatchingResult
from src.llm.prompts import DEBATE_SYSTEM_PROMPT, DEBATE_SUMMARY_SYSTEM_PROMPT


class AnalysisState(TypedDict, total=False):
    jd_text: str
    resume_text: str

    job_profile: JobProfile
    resume_profile: ResumeProfile

    # 预留：前置关键词匹配 / Tavily 情报 / 辩论等
    keyword_match: Dict[str, Any]
    tavily_insights: Dict[str, Any]

    matching_result: MatchingResult
    matching_refined: Dict[str, Any]

    # 大牛辩论与合议
    debate_wangchuan: Dict[str, Any]
    debate_naval: Dict[str, Any]
    debate_trump: Dict[str, Any]
    debate_rounds: list[Dict[str, Any]]
    final_competitiveness: Dict[str, Any]

    error: str


def _parse_jd_node(state: AnalysisState) -> AnalysisState:
    jd_text = state.get("jd_text", "")
    if not jd_text.strip():
        return {}
    try:
        job_profile, _ = parse_jd(jd_text)
        return {"job_profile": job_profile}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"parse_jd failed: {exc}"}


def _parse_resume_node(state: AnalysisState) -> AnalysisState:
    resume_text = state.get("resume_text", "")
    if not resume_text.strip():
        return {}
    try:
        resume_profile, _ = parse_resume(resume_text)
        return {"resume_profile": resume_profile}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"parse_resume failed: {exc}"}


def _keyword_match_node(state: AnalysisState) -> AnalysisState:
    """关键词匹配节点（暂不使用）：占位，当前返回空。"""
    return {}


def _tavily_search_node(state: AnalysisState) -> AnalysisState:
    """基于 JD / JobProfile 做 Tavily 搜索，聚焦公司 / 岗位 / 行业大环境."""
    job = state.get("job_profile")
    if job is None:
        return {}
    insights = run_tavily_search(job)
    return {"tavily_insights": insights}


def _match_core_node(state: AnalysisState) -> AnalysisState:
    job = state.get("job_profile")
    resume = state.get("resume_profile")
    jd_text = state.get("jd_text", "")
    resume_text = state.get("resume_text", "")
    if job is None or resume is None:
        return {}
    try:
        matching_result, matching_refined = compute_matching_with_details(
            resume,
            job,
            jd_text=jd_text or None,
            resume_text=resume_text or None,
        )
        return {
            "matching_result": matching_result,
            "matching_refined": matching_refined,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"matching failed: {exc}"}


def _debate_persona_node(state: AnalysisState, persona: str, state_key: str) -> AnalysisState:
    """单个大牛角色的辩论节点：根据 env 中的 DEBATE_PERSONAS 决定是否启用。

    为了支持在图中并行执行，每个 persona 节点只写入各自独立的 state_key，
    最终在 summary 节点中再统一汇总为 debate_rounds。
    """
    enabled_raw = os.getenv("DEBATE_PERSONAS", "wangchuan,naval,trump")
    enabled = {p.strip().lower() for p in enabled_raw.split(",") if p.strip()}
    if persona.lower() not in enabled:
        return {}

    jd_text = state.get("jd_text", "") or ""
    resume_text = state.get("resume_text", "") or ""
    matching_result = state.get("matching_result")
    matching_refined = state.get("matching_refined") or {}
    tavily_insights = state.get("tavily_insights") or {}

    if matching_result is None:
        return {}

    persona_styles = {
        "wangchuan": {
            "display_name": "王川",
            "style": "你现在扮演王川，从谨慎且略偏悲观的 HR/用人经理视角出发，更关注风险、机会成本和淘汰率，会结合当前行情偏冷、HC 紧缩，强调筛选标准偏高，说话理性、略带冷幽默。",
        },
        "naval": {
            "display_name": "Naval",
            "style": "你现在扮演 Naval，从相对乐观的长期主义投资人/用人方视角出发，更关注候选人的长期潜力、可放大的杠杆、未来成长空间，即使当前匹配度一般也会思考“值不值得押注”，说话简洁、有格局，用中文表达。",
        },
        "trump": {
            "display_name": "特朗普",
            "style": "你现在扮演特朗普，从一线 HR/老板非常现实甚至有点刻薄的视角出发，风格直接、敢说难听话、偏悲观一些，但所有吐槽都要有事实依据，结合 JD、简历和市场环境说明为什么可能拿不到 offer 或性价比一般。",
        },
    }
    style_conf = persona_styles.get(
        persona.lower(),
        {
            "display_name": persona,
            "style": f"你现在扮演 {persona} 这一角色，说话风格可以有个人色彩，但判断要有依据、接地气。",
        },
    )

    system_prompt = (
        DEBATE_SYSTEM_PROMPT
        + "\n\n当前角色设定：\n"
        + style_conf["style"]
    )

    mr_json = matching_result.model_dump(mode="json")
    mr_text = json.dumps(mr_json, ensure_ascii=False, indent=2)
    refined_text = json.dumps(matching_refined, ensure_ascii=False, indent=2)
    tavily_text = json.dumps(tavily_insights, ensure_ascii=False, indent=2)

    user_prompt = (
        "下面是本次评估所需的全部上下文，你需要基于它们做出对候选人竞争力的判断：\n\n"
        "【职位 JD 原文】\n"
        f"{jd_text}\n\n"
        "【候选人简历原文】\n"
        f"{resume_text}\n\n"
        "【规则层匹配结果 MatchingResult（JSON）】\n"
        f"{mr_text}\n\n"
        "【句子级语义对齐结果 matching_refined（JSON）】\n"
        f"{refined_text}\n\n"
        "【Tavily 公司/岗位/行业情报（JSON）】\n"
        f"{tavily_text}\n\n"
        "请严格按照 system 提示给出的 JSON 结构，输出一条本角色的判断。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    raw = llm_client.chat(messages, temperature=0.4)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
    except json.JSONDecodeError:
        return {}

    data.setdefault("persona", persona)
    data.setdefault("display_name", style_conf["display_name"])

    return {state_key: data}


def _debate_summary_node(state: AnalysisState) -> AnalysisState:
    """根据前面各大牛的发言，做一次合议总结。"""
    rounds: list[Dict[str, Any]] = []
    for key in ("debate_wangchuan", "debate_naval", "debate_trump"):
        value = state.get(key)
        if isinstance(value, dict) and value:
            rounds.append(value)

    if not rounds:
        return {}

    try:
        rounds_text = json.dumps(rounds, ensure_ascii=False, indent=2)
    except TypeError:
        # 出现非 JSON 可序列化对象时直接跳过总结
        return {}

    messages = [
        {"role": "system", "content": DEBATE_SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "下面是多位大牛嘉宾的 JSON 发言数组，请综合它们给出一个合议结论：\n\n"
                f"{rounds_text}\n\n"
                "严格按照 system 提示中的 JSON 结构输出。"
            ),
        },
    ]

    raw = llm_client.chat(messages, temperature=0.3)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        summary = json.loads(text)
        if not isinstance(summary, dict):
            return {}
    except json.JSONDecodeError:
        return {}

    return {
        "debate_rounds": rounds,
        "final_competitiveness": summary,
    }


_GRAPH: Optional[Any] = None


def _build_graph() -> Any:
    graph = StateGraph(AnalysisState)

    # 解析层
    graph.add_node("parse_jd", _parse_jd_node)
    graph.add_node("parse_resume", _parse_resume_node)

    # 前置并行层：关键词匹配与 Tavily 搜索（当前为空实现）
    graph.add_node("keyword_match", _keyword_match_node)
    graph.add_node("tavily_search", _tavily_search_node)

    # 核心匹配层
    graph.add_node("match_core", _match_core_node)

    # 大牛辩论层：各 persona 并行执行，最后在 summary 汇总
    graph.add_node("debate_wangchuan", lambda s: _debate_persona_node(s, "wangchuan", "debate_wangchuan"))
    graph.add_node("debate_naval", lambda s: _debate_persona_node(s, "naval", "debate_naval"))
    graph.add_node("debate_trump", lambda s: _debate_persona_node(s, "trump", "debate_trump"))
    graph.add_node("debate_summary", _debate_summary_node)

    # 边：当前采用简单串行结构，后续可按需改为真正并行
    graph.set_entry_point("parse_jd")
    graph.add_edge("parse_jd", "parse_resume")
    graph.add_edge("parse_resume", "keyword_match")
    graph.add_edge("keyword_match", "tavily_search")
    graph.add_edge("tavily_search", "match_core")

    # 从 match_core 发散，三个大牛并行评估
    graph.add_edge("match_core", "debate_wangchuan")
    graph.add_edge("match_core", "debate_naval")
    graph.add_edge("match_core", "debate_trump")

    # 所有大牛的结果汇总到 summary
    graph.add_edge("debate_wangchuan", "debate_summary")
    graph.add_edge("debate_naval", "debate_summary")
    graph.add_edge("debate_trump", "debate_summary")
    graph.add_edge("debate_summary", END)

    return graph.compile()


def get_graph() -> Any:
    global _GRAPH  # noqa: PLW0603
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def run_analysis(jd_text: str, resume_text: str) -> AnalysisState:
    """运行一次完整分析：解析 JD/简历 → 关键词匹配/Tavily（占位）→ 匹配 → 辩论（占位）."""
    graph = get_graph()
    initial_state: AnalysisState = {
        "jd_text": jd_text,
        "resume_text": resume_text,
    }
    final_state: AnalysisState = graph.invoke(initial_state)
    return final_state

