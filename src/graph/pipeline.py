from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict
import json
import os

from langgraph.graph import StateGraph, END

from src.llm.client import llm_client
from src.parsers.jd_parser import parse_jd_with_llm
from src.parsers.resume_parser import parse_resume_with_llm
from src.analysis.matching_engine import compute_matching_with_details
from src.analysis.tavily_search import run_tavily_search
from src.models.schemas import JobProfile, ResumeProfile, MatchingResult
from src.llm.prompts import (
    DEBATE_SYSTEM_PROMPT,
    DEBATE_STANCE_INSTRUCTION,
    DEBATE_SUMMARY_SYSTEM_PROMPT,
)
from src.llm.debate_personas import (
    get_persona,
    get_enabled_personas_from_env,
    draw_stance,
)


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

    # 大牛辩论与合议（debate_all 写入 debate_rounds）
    debate_personas_override: list[str]  # 可选，前端传入时优先于 DEBATE_PERSONAS
    debate_rounds: list[Dict[str, Any]]
    final_competitiveness: Dict[str, Any]

    error: str


def _parse_jd_node(state: AnalysisState) -> AnalysisState:
    jd_text = state.get("jd_text", "")
    if not jd_text.strip():
        return {}
    try:
        job_profile, _ = parse_jd_with_llm(jd_text)
        return {"job_profile": job_profile}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"parse_jd failed: {exc}"}


def _parse_resume_node(state: AnalysisState) -> AnalysisState:
    resume_text = state.get("resume_text", "")
    if not resume_text.strip():
        return {}
    try:
        resume_profile, _ = parse_resume_with_llm(resume_text)
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


def _run_single_debate(
    persona_id: str,
    jd_text: str,
    resume_text: str,
    matching_result: Any,
    matching_refined: Dict[str, Any],
    tavily_insights: Dict[str, Any],
) -> Dict[str, Any]:
    """对单个大牛执行一轮辩论，返回该角色的 JSON 结果（含 stance）。"""
    conf = get_persona(persona_id)
    if not conf:
        return {}
    stance = draw_stance()
    system_prompt = (
        DEBATE_SYSTEM_PROMPT
        + "\n\n"
        + DEBATE_STANCE_INSTRUCTION.format(stance=stance)
        + "\n\n当前角色设定：\n"
        + conf["description"]
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
    data.setdefault("persona", persona_id)
    data.setdefault("display_name", conf["display_name"])
    data["stance"] = stance
    return data


def _debate_all_node(state: AnalysisState) -> AnalysisState:
    """按人选顺序依次运行各大牛辩论，每人随机 50% 看好/看空，汇总为 debate_rounds。人选优先用 state 中的 debate_personas_override，否则用 DEBATE_PERSONAS。"""
    jd_text = state.get("jd_text", "") or ""
    resume_text = state.get("resume_text", "") or ""
    matching_result = state.get("matching_result")
    matching_refined = state.get("matching_refined") or {}
    tavily_insights = state.get("tavily_insights") or {}
    if matching_result is None:
        return {}
    override = state.get("debate_personas_override")
    enabled = [p.strip().lower() for p in override if p.strip()] if override else get_enabled_personas_from_env()
    if not enabled:
        return {}
    rounds: list[Dict[str, Any]] = []
    for persona_id in enabled:
        result = _run_single_debate(
            persona_id,
            jd_text,
            resume_text,
            matching_result,
            matching_refined,
            tavily_insights,
        )
        if result:
            rounds.append(result)
    return {"debate_rounds": rounds}


def _debate_summary_node(state: AnalysisState) -> AnalysisState:
    """根据前面各大牛的发言，做一次合议总结。"""
    rounds: list[Dict[str, Any]] = list(state.get("debate_rounds") or [])

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

    # 大牛辩论层：单节点按 DEBATE_PERSONAS 顺序依次跑各大牛（每人随机看好/看空），再 summary
    graph.add_node("debate_all", _debate_all_node)
    graph.add_node("debate_summary", _debate_summary_node)

    # 边
    graph.set_entry_point("parse_jd")
    graph.add_edge("parse_jd", "parse_resume")
    graph.add_edge("parse_resume", "keyword_match")
    graph.add_edge("keyword_match", "tavily_search")
    graph.add_edge("tavily_search", "match_core")
    graph.add_edge("match_core", "debate_all")
    graph.add_edge("debate_all", "debate_summary")
    graph.add_edge("debate_summary", END)

    return graph.compile()


def get_graph() -> Any:
    global _GRAPH  # noqa: PLW0603
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def run_analysis(
    jd_text: str,
    resume_text: str,
    debate_personas_override: list[str] | None = None,
) -> AnalysisState:
    """运行一次完整分析。debate_personas_override 非空时优先于 .env 的 DEBATE_PERSONAS（如前端多选）。"""
    graph = get_graph()
    initial_state: AnalysisState = {
        "jd_text": jd_text,
        "resume_text": resume_text,
    }
    if debate_personas_override:
        initial_state["debate_personas_override"] = debate_personas_override
    final_state: AnalysisState = graph.invoke(initial_state)
    return final_state

