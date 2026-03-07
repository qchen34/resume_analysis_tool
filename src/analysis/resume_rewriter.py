from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from pydantic import ValidationError

from src.llm.client import llm_client
from src.llm.prompts import (
    RESUME_REWRITE_SYSTEM_PROMPT,
    RESUME_REWRITE_REVIEW_SYSTEM_PROMPT,
)
from src.models.schemas import JobProfile, MatchingResult, ResumeChange, RewriteResult


def _strip_markdown_fences(content: str) -> str:
    """去掉可能的 ```json ... ``` 包裹，提取纯 JSON 文本。"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _build_rewrite_user_prompt(
    raw_resume_text: str,
    job: JobProfile,
    matching: MatchingResult,
) -> str:
    """组装简历重写的 user prompt：JD 摘要、匹配差距、原简历。"""
    jd_lines = [
        f"岗位：{job.role_title or '未提供'}",
        f"公司：{job.company or '未提供'}",
        f"级别/部门：{job.level or '-'}，{job.department or '-'}",
        f"必备技能：{', '.join(job.must_have_skills[:15]) or '无'}",
        f"加分技能：{', '.join(job.nice_to_have_skills[:10]) or '无'}",
        f"经验要求：{job.experience_requirements or '未明确'}",
        "核心职责（前几条）：",
    ]
    for i, r in enumerate(job.responsibilities[:6], 1):
        jd_lines.append(f"  {i}. {r}")

    gap_lines = [
        f"- [{g.severity}] {g.type}: {g.name}" + (f" — {g.detail}" if g.detail else "")
        for g in matching.gaps[:20]
    ]
    score_str = (
        str(round(matching.overall_score, 1))
        if matching.overall_score is not None
        else str(round(matching.overall_score_raw * 100, 1))
    )

    return (
        "请根据以下目标 JD 与当前简历的匹配差距，对简历进行优化。\n\n"
        "【目标 JD 摘要】\n"
        + "\n".join(jd_lines)
        + "\n\n"
        "【当前匹配情况】\n"
        f"综合匹配分（约）：{score_str}/100\n"
        "主要差距（请重点在重写时弥补或弱化）：\n"
        + ("\n".join(gap_lines) if gap_lines else "  （无结构化差距列表）")
        + "\n\n"
        "【原始简历全文】\n"
        "-----\n"
        f"{raw_resume_text}\n"
        "-----\n\n"
        "请输出一个 JSON 对象，包含 revised_resume_text（完整修改后的简历）和 changes（变更列表）。"
    )


def _extract_first_json_object(text: str) -> dict:
    """
    从文本中解析第一个完整的 JSON 对象并返回。
    LLM 可能在 JSON 后继续输出多余内容，导致「Extra data」错误，此处只取首对象。
    """
    cleaned = text.strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("文本中未找到 JSON 对象起始符 '{'")
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"从第一个 '{{' 起解析 JSON 失败: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("解析结果不是 JSON 对象")
    return obj


def _parse_llm_rewrite_response(raw_output: str) -> RewriteResult:
    """解析 LLM 返回的 JSON 为 RewriteResult。"""
    cleaned = _strip_markdown_fences(raw_output)
    try:
        data = _extract_first_json_object(cleaned)
    except ValueError as exc:
        raise RuntimeError(
            f"简历重写 LLM 返回的不是合法 JSON: {exc}\n输出片段:\n{raw_output[:1500]}"
        ) from exc

    revised = data.get("revised_resume_text")
    if revised is None or not isinstance(revised, str):
        raise RuntimeError(
            "简历重写 JSON 缺少有效的 revised_resume_text 字符串。"
        )

    raw_changes: List[Dict[str, Any]] = data.get("changes") or []
    changes: List[ResumeChange] = []
    for i, c in enumerate(raw_changes):
        if not isinstance(c, dict):
            continue
        item_index = c.get("item_index")
        if item_index is not None:
            try:
                item_index = int(item_index)
            except (TypeError, ValueError):
                item_index = None
        try:
            changes.append(
                ResumeChange(
                    section=str(c.get("section") or "unknown"),
                    item_index=item_index,
                    change_type=str(c.get("change_type") or "edit"),
                    old_text=str(c["old_text"]) if c.get("old_text") is not None else None,
                    new_text=str(c["new_text"]) if c.get("new_text") is not None else None,
                )
            )
        except (TypeError, ValueError) as e:
            raise RuntimeError(
                f"简历重写 changes[{i}] 解析失败: {e}，内容: {c}"
            ) from e

    try:
        return RewriteResult(revised_resume_text=revised.strip(), changes=changes)
    except ValidationError as exc:
        raise RuntimeError(
            f"简历重写结果不符合 RewriteResult 模型: {exc}"
        ) from exc


def _review_rewrite_for_fluency(revised_resume_text: str, job: JobProfile) -> str:
    """
    对重写后的简历做一轮「通顺性/可读性」审核，减轻关键词堆砌、使表述更自然。
    只返回润色后的完整简历正文，不改变 changes 列表（仍以首轮重写的变更意图为准）。
    """
    job_summary = (
        f"岗位：{job.role_title or '未提供'}；公司：{job.company or '未提供'}；"
        f"必备技能：{', '.join(job.must_have_skills[:10]) or '无'}；"
        f"核心职责（前 3 条）：{' | '.join(job.responsibilities[:3]) or '无'}"
    )
    user_prompt = (
        "下面是一份已根据目标 JD 优化过的简历全文，请在不改变事实与结构的前提下，"
        "对生硬或堆砌关键词的句子做润色，使全文读起来更自然、通顺。\n\n"
        "目标 JD 摘要（供理解语境）：\n"
        f"{job_summary}\n\n"
        "简历全文：\n-----\n"
        f"{revised_resume_text}\n"
        "-----\n\n"
        "请只输出润色后的完整简历正文，不要输出 JSON、解释或 Markdown 代码块。"
    )
    messages = [
        {"role": "system", "content": RESUME_REWRITE_REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw_output = llm_client.chat(messages, temperature=0.2)
    return raw_output.strip()


def rewrite_resume_for_job(
    raw_resume_text: str,
    job: JobProfile,
    matching: MatchingResult,
) -> RewriteResult:
    """
    根据目标 JD 与匹配差距，调用 LLM 重写简历，返回修改后全文与结构化变更列表。
    若设置环境变量 ENABLE_REWRITE_REVIEW=true（默认 true），会在首轮重写后再做一轮「通顺性审核」，
    用审核后的正文作为最终 revised_resume_text，减轻关键词堆砌、提升可读性。
    """
    user_prompt = _build_rewrite_user_prompt(raw_resume_text, job, matching)
    messages = [
        {"role": "system", "content": RESUME_REWRITE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw_output = llm_client.chat(messages, temperature=0.3)
    result = _parse_llm_rewrite_response(raw_output)

    # 可选：重写后再做一层通顺性/可读性审核，替换为润色后的正文
    enable_review = os.getenv("ENABLE_REWRITE_REVIEW", "true").lower() == "true"
    if enable_review and result.revised_resume_text:
        try:
            result = RewriteResult(
                revised_resume_text=_review_rewrite_for_fluency(
                    result.revised_resume_text, job
                ),
                changes=result.changes,
            )
        except Exception:
            # 审核失败时保留首轮重写结果，不打断流程
            pass

    return result
