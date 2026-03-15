from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime
import os
import hashlib
import json
import traceback

from dotenv import load_dotenv

# 从脚本所在目录加载 .env，避免因运行目录不同而读不到配置
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path)

from src.analysis.matching_engine import compute_matching, compute_matching_with_details
from src.analysis.tavily_search import run_tavily_search
from src.analysis.resume_rewriter import rewrite_resume_for_job
from src.llm.client import llm_client
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
from src.parsers.jd_parser import parse_jd
from src.parsers.resume_parser import parse_resume
from src.models.schemas import JobProfile, ResumeProfile, MatchingResult
from src.ocr.ocr_utils import (
    extract_text_auto,
    find_ocr_sources,
    get_jd_and_resume_from_input,
    get_input_dir,
)


def _usage_snapshot() -> Dict[str, Optional[int]]:
    usage = getattr(llm_client, "last_usage", None)
    if not usage:
        return {"input_tokens": None, "output_tokens": None, "total": None}
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", input_tokens)
        output_tokens = usage.get("output_tokens", output_tokens)
    total = (input_tokens or 0) + (output_tokens or 0) if (input_tokens is not None or output_tokens is not None) else None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total": total,
    }


def _run_debate_for_persona(
    persona_id: str,
    jd_text: str,
    resume_text: str,
    matching_result: MatchingResult,
    matching_refined: Dict[str, Any],
    tavily_insights: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """对单个大牛角色运行一轮辩论，返回该 persona 的 JSON 结果（含随机看好/看空）。"""
    conf = get_persona(persona_id)
    if not conf:
        return {}
    tavily_insights = tavily_insights or {}
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
    refined_text = json.dumps(matching_refined or {}, ensure_ascii=False, indent=2)
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


def _summarize_debate(rounds: list[Dict[str, Any]]) -> Dict[str, Any]:
    """对多位大牛的发言做一次合议总结。"""
    if not rounds:
        return {}

    try:
        rounds_text = json.dumps(rounds, ensure_ascii=False, indent=2)
    except TypeError:
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

    return summary


def _run_ocr_examples(base_dir: Path, output_dir: Path) -> None:
    """
    从 input 目录中查找图片/PDF 文件，做 OCR + 规则解析，
    并将 JD/简历的解析结果以 Markdown 报告形式输出，便于人工检查 OCR 质量。
    """
    example_dir = base_dir / "input"
    sources = find_ocr_sources(example_dir)
    if not sources:
        return

    jd_sections: list[str] = []
    resume_sections: list[str] = []

    for p in sources:
        try:
            text = extract_text_auto(p)
        except Exception as exc:  # noqa: BLE001
            section = (
                f"### 文件: {p.name}\n\n"
                f"- **状态**: OCR 失败（{exc}）\n"
            )
            if "jd" in p.stem.lower():
                jd_sections.append(section)
            elif "resume" in p.stem.lower() or "cv" in p.stem.lower():
                resume_sections.append(section)
            continue

        # 简单根据文件名中的关键字判断是 JD 还是简历
        lowered = p.stem.lower()
        if "jd" in lowered or "job" in lowered:
            try:
                job_profile, job_json = parse_jd(text)
                section = (
                    f"### 文件: {p.name}\n\n"
                    "#### OCR 提取文本（截断预览）\n\n"
                    "```text\n"
                    f"{text[:1000]}\n"
                    "```\n\n"
                    "#### 规则解析 JobProfile（repr）\n\n"
                    f"`{repr(job_profile)}`\n\n"
                    "#### 解析 JSON\n\n"
                    "```json\n"
                    f"{job_json}\n"
                    "```\n"
                )
            except Exception as exc:  # noqa: BLE001
                section = (
                    f"### 文件: {p.name}\n\n"
                    "- **状态**: OCR 成功但 parse_jd 失败。\n"
                    f"- 错误: {exc}\n"
                )
            jd_sections.append(section)
        elif "resume" in lowered or "cv" in lowered:
            try:
                resume_profile, resume_json = parse_resume(text)
                section = (
                    f"### 文件: {p.name}\n\n"
                    "#### OCR 提取文本（截断预览）\n\n"
                    "```text\n"
                    f"{text[:1000]}\n"
                    "```\n\n"
                    "#### 规则解析 ResumeProfile（repr）\n\n"
                    f"`{repr(resume_profile)}`\n\n"
                    "#### 解析 JSON\n\n"
                    "```json\n"
                    f"{resume_json}\n"
                    "```\n"
                )
            except Exception as exc:  # noqa: BLE001
                section = (
                    f"### 文件: {p.name}\n\n"
                    "- **状态**: OCR 成功但 parse_resume 失败。\n"
                    f"- 错误: {exc}\n"
                )
            resume_sections.append(section)

    if jd_sections:
        jd_ocr_path = output_dir / "parse_jd_ocr.md"
        content = ["# OCR + 规则解析 JD 报告\n"] + jd_sections
        jd_ocr_path.write_text("\n".join(content), encoding="utf-8")

    if resume_sections:
        resume_ocr_path = output_dir / "parse_resume_ocr.md"
        content = ["# OCR + 规则解析 简历 报告\n"] + resume_sections
        resume_ocr_path.write_text("\n".join(content), encoding="utf-8")


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    # 再次从项目根加载 .env，避免被其他模块的 load_dotenv()（从 cwd 加载）覆盖
    load_dotenv(base_dir / ".env", override=True)

    # 统一从 input 目录读取 JD 与简历（唯一入口）
    jd_path, resume_path = get_jd_and_resume_from_input(base_dir)
    if jd_path is None or resume_path is None:
        raise FileNotFoundError(
            "未在 input 目录找到 JD 或简历。请将文件放入项目根目录下的 input/ 中，"
            "文件名需含 jd 或 job（JD）、resume 或 cv（简历），支持 PDF 与常见图片格式。"
        )
    _model = os.getenv("GEMINI_MODEL") or getattr(llm_client, "_default_model", "gemini-3-flash-preview")
    print("[1/8] 读取 JD 与简历（OCR 提取文本）")
    print(f"      入口: input/ → JD={jd_path.name}, 简历={resume_path.name}")
    print(f"      调用: Gemini API（{_model}）— Flash 多模态 OCR")
    jd_text = extract_text_auto(jd_path)
    resume_text = extract_text_auto(resume_path)
    print("      完成")

    # 基于文本内容的缓存键，避免重复请求 LLM
    cache_root = base_dir / "test_outputs" / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    key_str = jd_text + "\n====RESUME====\n" + resume_text
    cache_key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    cache_path = cache_root / f"{cache_key}.json"

    force_reanalyze = os.getenv("FORCE_REANALYZE", "false").lower() == "true"

    tavily_insights: Dict[str, Any] | None = None
    debate_rounds: list[Dict[str, Any]] | None = None
    final_competitiveness: Dict[str, Any] | None = None

    if not force_reanalyze and cache_path.exists():
        print("[2/8] 使用缓存跳过解析与匹配（设 FORCE_REANALYZE=true 可强制重新执行）")
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        job_profile = JobProfile.model_validate(data["job_profile"])
        job_json = data["job_json"]
        jd_usage = data["jd_usage"]

        resume_profile = ResumeProfile.model_validate(data["resume_profile"])
        resume_json = data["resume_json"]
        resume_usage = data["resume_usage"]

        matching_result = MatchingResult.model_validate(data["matching_result"])
        matching_refined = data["matching_refined"]
        match_usage = data["match_usage"]
        tavily_insights = data.get("tavily_insights")
        debate_rounds = data.get("debate_rounds")
        final_competitiveness = data.get("final_competitiveness")
    else:
        print("[2/8] 解析 JD")
        print("      方式: 规则解析，无 API 调用")
        job_profile, job_json = parse_jd(jd_text)
        jd_usage = _usage_snapshot()
        print("      完成")

        print("[3/8] 解析简历")
        print("      方式: 规则解析，无 API 调用")
        resume_profile, resume_json = parse_resume(resume_text)
        resume_usage = _usage_snapshot()
        print("      完成")

        _tavily_key = os.getenv("TAVILY_API_KEY", "")
        print("[4/8] Tavily 搜索（公司/岗位/行业情报）")
        print(f"      调用: Tavily API（已配置密钥: {'是' if _tavily_key else '否'}，未配置则仅生成查询计划）")
        tavily_insights = run_tavily_search(job_profile)
        print("      完成")

        _match_model = os.getenv("GEMINI_MODEL") or getattr(llm_client, "_default_model", "")
        print("[5/8] 匹配分析（规则维度 + 句子级语义对齐）")
        print(f"      调用: Gemini API（{_match_model}）— 语义对齐")
        matching_result, matching_refined = compute_matching_with_details(
            resume_profile,
            job_profile,
            jd_text=jd_text,
            resume_text=resume_text,
        )
        match_usage = _usage_snapshot()
        print("      完成")

        _debate_model = os.getenv("GEMINI_MODEL") or getattr(llm_client, "_default_model", "")
        print("[6/8] 大牛辩论与合议")
        print(f"      调用: Gemini API（{_debate_model}）；每人随机 50% 看好/看空")
        debate_rounds = []
        personas = get_enabled_personas_from_env()
        for p in personas:
            conf = get_persona(p)
            _dn = conf.get("display_name", p) if conf else p
            print(f"      → 角色: {_dn}")
            result = _run_debate_for_persona(
                p,
                jd_text=jd_text,
                resume_text=resume_text,
                matching_result=matching_result,
                matching_refined=matching_refined,
                tavily_insights=tavily_insights,
            )
            if result:
                debate_rounds.append(result)
        print(f"      → 合议总结: Gemini API（{_debate_model}）")
        final_competitiveness = _summarize_debate(debate_rounds or [])
        print("      完成")

        cache_data: Dict[str, Any] = {
            "job_profile": job_profile.model_dump(mode="json"),
            "job_json": job_json,
            "jd_usage": jd_usage,
            "resume_profile": resume_profile.model_dump(mode="json"),
            "resume_json": resume_json,
            "resume_usage": resume_usage,
            "matching_result": matching_result.model_dump(mode="json"),
            "matching_refined": matching_refined,
            "match_usage": match_usage,
            "tavily_insights": tavily_insights,
            "debate_rounds": debate_rounds,
            "final_competitiveness": final_competitiveness,
        }
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[7/8] 生成 Markdown 报告")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = base_dir / "test_outputs"
    output_dir = output_root / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    # 4.1 JD 分析报告（当前阶段仅输出 JD 原文）
    jd_lines: list[str] = []
    jd_lines.append("# JD 分析报告\n")
    jd_lines.append(f"- **JD 文件**: `{jd_path}`\n")
    jd_lines.append("## JD 原文\n")
    jd_lines.append("```markdown")
    jd_lines.append(jd_text.strip())
    jd_lines.append("```")
    jd_report_path = output_dir / "jd_analysis.md"
    jd_report_path.write_text("\n".join(jd_lines), encoding="utf-8")

    # 4.2 简历分析报告（当前阶段仅输出简历原文）
    resume_lines: list[str] = []
    resume_lines.append("# 简历分析报告\n")
    resume_lines.append(f"- **简历文件**: `{resume_path}`\n")
    resume_lines.append("## 简历原文\n")
    resume_lines.append("```markdown")
    resume_lines.append(resume_text.strip())
    resume_lines.append("```")
    resume_report_path = output_dir / "resume_analysis.md"
    resume_report_path.write_text("\n".join(resume_lines), encoding="utf-8")

    # 4.3 Tavily 情报报告（聚焦公司 / 岗位 / 行业，而非简历）
    tavily_lines: list[str] = []
    tavily_lines.append("# Tavily 搜索情报报告\n")
    tavily_lines.append(f"- **JD 文件**: `{jd_path}`\n")
    tavily_lines.append("- **说明**: 本报告仅关注公司、岗位与行业大环境的信息，不使用简历中的任何内容。\n")

    insights = tavily_insights or {}
    enabled = bool(insights.get("enabled"))

    # 4.3.1 搜索策略说明：我们是如何从 JD 中抽取关键词并构造查询的
    tavily_lines.append("## 一、搜索策略（我们如何从 JD 构造查询）\n")
    tavily_lines.append(
        "- 优先使用 JD 结构化解析结果中的公司名、岗位名称、地点、领域关键词（`domain_keywords`）来构造查询；\n"
    )
    tavily_lines.append(
        "- 将查询分为「公司介绍」「岗位职责/日常工作」「面试经验与难度」「行业 / 技术大环境」四类，分别调用 Tavily；\n"
    )
    tavily_lines.append(
        "- 每一类都会明确写出所用关键词和最终发送给 Tavily 的完整 Query，方便你理解情报是如何得到的。\n"
    )

    queries = insights.get("queries") or []
    if not enabled:
        reason = insights.get("reason") or "Tavily 未启用。"
        tavily_lines.append(f"\n> 当前未实际调用 Tavily：{reason}\n")

    if queries:
        tavily_lines.append("\n### 查询明细\n")
        tavily_lines.append("| 查询类型 | 使用关键词 | 完整 Query |")
        tavily_lines.append("|----------|------------|------------|")
        for q in queries:
            q_type = q.get("type", "")
            keywords = q.get("keywords", "")
            query_text = q.get("query", "")
            tavily_lines.append(f"| {q_type} | {keywords} | {query_text} |")
    else:
        tavily_lines.append("\n_当前未生成任何 Tavily 查询计划（可能是 JD 中缺少公司 / 岗位等关键信息）。_\n")

    # 4.3.2 实际搜索结果（若已启用 Tavily）
    tavily_lines.append("\n## 二、搜索结果摘要（按查询类型聚合）\n")
    results = insights.get("search_results") or []
    if enabled and results:
        for idx, r in enumerate(results, 1):
            r_type = r.get("type", "")
            query = r.get("query", "")
            summary = r.get("summary", "")
            error = r.get("error")

            tavily_lines.append(f"### {idx}. [{r_type}] {query}\n")
            if error:
                tavily_lines.append(f"- **状态**: 调用失败（{error}）\n")
                continue
            if summary:
                tavily_lines.append(f"- **Tavily 摘要**: {summary}\n")
            links = r.get("links") or []
            if links:
                tavily_lines.append("- **参考链接（Top 3）**:")
                for link in links:
                    title = link.get("title") or link.get("url") or ""
                    url = link.get("url") or ""
                    tavily_lines.append(f"  - [{title}]({url})")
            tavily_lines.append("")
    else:
        tavily_lines.append("_当前没有可用的 Tavily 搜索结果，仅展示了搜索策略。_\n")

    tavily_report_path = output_dir / "tavily_report.md"
    tavily_report_path.write_text("\n".join(tavily_lines), encoding="utf-8")

    # 4.4 匹配结果报告（句子级语义对齐数据）
    match_lines: list[str] = []
    match_lines.append("# Matching 报告\n")
    match_lines.append(f"- **JD 文件**: `{jd_path}`")
    match_lines.append(f"- **简历文件**: `{resume_path}`\n")

    # 职责语义对齐
    resp_matches = (matching_refined or {}).get("responsibility_semantic_matches")
    match_lines.append("## 职责语义对齐（仅数据）\n")
    if resp_matches:
        match_lines.append("| JD 职责 | 对应简历句子示例 |")
        match_lines.append("|---------|------------------|")
        for item in resp_matches:
            jd_item = item.get("jd_item", "")
            sentences = item.get("resume_sentences") or []
            preview = "；".join(sentences[:2])
            match_lines.append(f"| {jd_item} | {preview} |")
        match_lines.append("\n原始 JSON 数据：")
        match_lines.append("```json")
        match_lines.append(json.dumps({"responsibility_semantic_matches": resp_matches}, ensure_ascii=False, indent=2))
        match_lines.append("```")
    else:
        match_lines.append("_本次未生成职责语义对齐数据。_\n")

    # 任职要求语义对齐
    req_matches = (matching_refined or {}).get("requirement_semantic_matches")
    match_lines.append("## 任职要求语义对齐（仅数据）\n")
    if req_matches:
        match_lines.append("| JD 要求 | 对应简历句子示例 |")
        match_lines.append("|---------|------------------|")
        for item in req_matches:
            jd_req = item.get("jd_requirement", "")
            sentences = item.get("resume_sentences") or []
            preview = "；".join(sentences[:2])
            match_lines.append(f"| {jd_req} | {preview} |")
        match_lines.append("\n原始 JSON 数据：")
        match_lines.append("```json")
        match_lines.append(json.dumps({"requirement_semantic_matches": req_matches}, ensure_ascii=False, indent=2))
        match_lines.append("```")
    else:
        match_lines.append("_本次未生成任职要求语义对齐数据。_\n")

    match_report_path = output_dir / "matching_report.md"
    match_report_path.write_text("\n".join(match_lines), encoding="utf-8")

    # 4.5 大牛辩论报告
    debate_lines: list[str] = []
    debate_lines.append("# 大牛辩论与合议报告\n")
    debate_lines.append(f"- **JD 文件**: `{jd_path}`")
    debate_lines.append(f"- **简历文件**: `{resume_path}`\n")

    personas = get_enabled_personas_from_env()
    debate_lines.append("## 使用的大牛角色\n")
    if personas:
        debate_lines.append(", ".join(personas) + "\n")
    else:
        debate_lines.append("_当前未配置任何大牛角色（DEBATE_PERSONAS 为空）。_\n")

    rounds = debate_rounds or []
    if rounds:
        debate_lines.append("## 各大牛个人观点\n")
        for idx, r in enumerate(rounds, 1):
            display_name = r.get("display_name") or r.get("persona") or f"persona_{idx}"
            verdict = r.get("verdict") or "（未给出结论）"
            confidence = r.get("confidence")
            analysis = r.get("analysis") or ""
            advice = r.get("advice_to_candidate") or ""

            debate_lines.append(f"### {idx}. {display_name}\n")
            stance = r.get("stance")
            if stance:
                debate_lines.append(f"- **本次立场**: {stance}")
            debate_lines.append(f"- **结论（verdict）**: {verdict}")
            if confidence is not None:
                debate_lines.append(f"- **信心程度（0-1）**: {confidence}")
            if analysis:
                debate_lines.append(f"- **详细分析**:\n\n{analysis}\n")
            if advice:
                debate_lines.append(f"- **给候选人的建议**:\n\n{advice}\n")
    else:
        debate_lines.append("## 各大牛个人观点\n")
        debate_lines.append("_本次未生成任何大牛辩论结果（可能是 DEBATE_PERSONAS 为空或调用失败）。_\n")

    summary = final_competitiveness or {}
    debate_lines.append("## 合议总结\n")
    if summary:
        overall = summary.get("overall_verdict") or "（未给出整体结论）"
        debate_lines.append(f"- **整体结论**: {overall}\n")

        points = summary.get("summary_points") or []
        if isinstance(points, list) and points:
            debate_lines.append("- **总结要点**:")
            for p in points:
                debate_lines.append(f"  - {p}")

        strategy = summary.get("suggested_strategy") or ""
        if strategy:
            debate_lines.append("\n- **推荐策略**:\n")
            debate_lines.append(strategy)
    else:
        debate_lines.append("_本次未生成合议总结。_\n")

    debate_report_path = output_dir / "debate_report.md"
    debate_report_path.write_text("\n".join(debate_lines), encoding="utf-8")

    # 4.6 OCR 示例解析报告（如 input 下另有图片/PDF 可作示例）
    print("      [附加] OCR 示例解析（input 目录内图片/PDF）...")
    _run_ocr_examples(base_dir, output_dir)

    print(f"      报告目录: {output_dir}")

    # print("[6/7] 简历重写（含可选通顺性审核）...")
    # rewrite_result = rewrite_resume_for_job(resume_text, job_profile, matching_result)
    # rewrite_usage = _usage_snapshot()
    #
    # # 5.1 修改后的完整简历
    # rewritten_lines: list[str] = []
    # rewritten_lines.append("# 针对当前 JD 优化后的简历\n")
    # rewritten_lines.append(f"- **对应 JD**: `{jd_path}`\n")
    # rewritten_lines.append("---\n\n")
    # rewritten_lines.append(rewrite_result.revised_resume_text)
    # rewritten_path = output_dir / "rewritten_resume.md"
    # rewritten_path.write_text("\n".join(rewritten_lines), encoding="utf-8")
    #
    # # 5.2 重写变更报告（修改清单）
    # change_lines: list[str] = []
    # change_lines.append("# 简历重写变更报告\n")
    # change_lines.append(f"- **对应 JD**: `{jd_path}`")
    # change_lines.append(f"- **简历文件**: `{resume_path}`\n")
    # change_lines.append("## 变更清单\n")
    # for i, c in enumerate(rewrite_result.changes, 1):
    #     idx_str = f" 条目 {c.item_index}" if c.item_index is not None else ""
    #     change_lines.append(f"### {i}. [{c.change_type}] {c.section}{idx_str}\n")
    #     if c.old_text:
    #         change_lines.append(f"- **原文**: {c.old_text}\n")
    #     if c.new_text:
    #         change_lines.append(f"- **修改后**: {c.new_text}\n")
    # change_lines.append(
    #     f"- **Token 使用（ResumeRewriter）**: input={rewrite_usage['input_tokens']}, "
    #     f"output={rewrite_usage['output_tokens']}, total={rewrite_usage['total']}\n"
    # )
    # rewrite_report_path = output_dir / "rewrite_report.md"
    # rewrite_report_path.write_text("\n".join(change_lines), encoding="utf-8")
    # print("      完成")

    print("[8/8] 收尾与入库...")
    _raw = os.getenv("SAVE_TO_DB", "")
    save_to_db = (_raw or "").strip().lower() == "true"
    if not save_to_db:
        print(f"      [提示] 当前 SAVE_TO_DB={_raw!r}（.env 路径: {base_dir / '.env'}，存在: {(base_dir / '.env').exists()}）")
    elif save_to_db:
        print("      SAVE_TO_DB=true，正在写入数据库...")
    print("报告已生成:")
    print(f"- JD 报告: {jd_report_path}")
    print(f"- 简历报告: {resume_report_path}")
    print(f"- Tavily 报告: {tavily_report_path}")
    print(f"- Matching 报告: {match_report_path}")
    print(f"- Debate 报告: {debate_report_path}")
    if save_to_db:
        try:
            from src.db.analysis_run_repo import save_analysis_run
            job_id, resume_id, analysis_id, rewritten_id = save_analysis_run(
                jd_text=jd_text,
                resume_text=resume_text,
                job_profile=job_profile,
                resume_profile=resume_profile,
                matching_result=matching_result,
                matching_refined=matching_refined,
                rewrite_result=None,
                user_id=None,
                llm_model=os.getenv("GEMINI_MODEL"),
            )
            print(f"      已入库: job_id={job_id}, resume_id={resume_id}, analysis_id={analysis_id}, rewritten_resume_id={rewritten_id}")
        except Exception as e:
            print(f"      入库失败（报告已生成）: {e}")
            traceback.print_exc()
    else:
        print("      未启用入库（SAVE_TO_DB≠true）。若需写入数据库请在 .env 中设置 SAVE_TO_DB=true")


if __name__ == "__main__":
    main()
