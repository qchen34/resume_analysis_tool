"""
CLI 一次完整分析流程：从 input 读 JD/简历 → OCR → 解析 → Tavily → 匹配 → 大牛辩论 → 报告 → 入库。
供项目根目录 main.py 调用，保持 main.py 仅作入口。
"""
from __future__ import annotations

import hashlib
import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from src.analysis.matching_engine import compute_matching_with_details
from src.analysis.tavily_search import run_tavily_search
from src.analysis.tavily_summary import summarize_company_from_tavily
from src.llm.client import llm_client
from src.llm.debate_personas import (
    draw_stance,
    get_enabled_personas_from_env,
    get_persona,
)
from src.llm.prompts import (
    DEBATE_STANCE_INSTRUCTION,
    DEBATE_SUMMARY_SYSTEM_PROMPT,
    DEBATE_SYSTEM_PROMPT,
)
from src.models.schemas import JobProfile, MatchingResult, ResumeProfile
from src.ocr.ocr_utils import (
    extract_jd_structured,
    extract_resume_structured,
    get_jd_and_resume_from_input,
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


def _write_reports(
    output_dir: Path,
    jd_path: Path,
    resume_path: Path,
    jd_text: str,
    resume_text: str,
    job_profile: JobProfile,
    resume_profile: ResumeProfile,
    tavily_insights: Dict[str, Any],
    matching_refined: Dict[str, Any],
    debate_rounds: list[Dict[str, Any]],
    final_competitiveness: Dict[str, Any],
) -> Dict[str, Path]:
    """生成所有 Markdown 报告，返回报告路径字典。报告仅含 OCR 原文 + 结构化结果。"""
    jd_structured_json = json.dumps(job_profile.model_dump(mode="json"), ensure_ascii=False, indent=2)
    jd_lines = [
        "# JD 分析报告\n",
        f"- **JD 文件**: `{jd_path}`\n",
        "## 一、OCR 原文\n",
        "```markdown",
        jd_text.strip(),
        "```",
        "\n## 二、结构化结果\n",
        "以下由 OCR 后经 LLM 解析得到，供 Tavily 与匹配等下游使用。\n",
        "```json",
        jd_structured_json,
        "```",
    ]
    jd_report_path = output_dir / "jd_analysis.md"
    jd_report_path.write_text("\n".join(jd_lines), encoding="utf-8")

    resume_structured_json = json.dumps(resume_profile.model_dump(mode="json"), ensure_ascii=False, indent=2)
    resume_lines = [
        "# 简历分析报告\n",
        f"- **简历文件**: `{resume_path}`\n",
        "## 一、OCR 原文\n",
        "```markdown",
        resume_text.strip(),
        "```",
        "\n## 二、结构化结果\n",
        "以下由 OCR 后经 LLM 解析得到。\n",
        "```json",
        resume_structured_json,
        "```",
    ]
    resume_report_path = output_dir / "resume_analysis.md"
    resume_report_path.write_text("\n".join(resume_lines), encoding="utf-8")

    # Tavily 报告：策略 + 结果摘要 + Fast 模型综合解读
    insights = tavily_insights or {}
    enabled = bool(insights.get("enabled"))
    tavily_lines = [
        "# Tavily 搜索情报报告\n",
        f"- **JD 文件**: `{jd_path}`\n",
        "- **说明**: 本报告仅关注公司、岗位与行业大环境的信息，不使用简历中的任何内容。\n",
        "## 一、搜索策略（我们如何从 JD 构造查询）\n",
        "- 优先使用 JD 结构化解析结果中的公司名、岗位名称、地点、领域关键词（`domain_keywords`）来构造查询；\n",
        "- 将查询分为「公司介绍/规模」「岗位职责」「面试经验」「行业/竞争格局」等类，分别调用 Tavily；\n",
        "- 每一类都会明确写出所用关键词和最终发送给 Tavily 的完整 Query。\n",
    ]
    queries = insights.get("queries") or []
    if not enabled:
        reason = insights.get("reason") or "Tavily 未启用。"
        tavily_lines.append(f"\n> 当前未实际调用 Tavily：{reason}\n")
    if queries:
        tavily_lines.append("\n### 查询明细\n")
        tavily_lines.append("| 查询类型 | 使用关键词 | 完整 Query |")
        tavily_lines.append("|----------|------------|------------|")
        for q in queries:
            tavily_lines.append(
                f"| {q.get('type', '')} | {q.get('keywords', '')} | {q.get('query', '')} |"
            )
    else:
        tavily_lines.append("\n_当前未生成任何 Tavily 查询计划。_\n")

    tavily_lines.append("\n## 二、搜索结果摘要（按查询类型聚合）\n")
    results = insights.get("search_results") or []
    if enabled and results:
        for idx, r in enumerate(results, 1):
            tavily_lines.append(f"### {idx}. [{r.get('type','')}] {r.get('query','')}\n")
            if r.get("error"):
                tavily_lines.append(f"- **状态**: 调用失败（{r['error']}）\n")
                continue
            if r.get("summary"):
                tavily_lines.append(f"- **Tavily 摘要**: {r['summary']}\n")
            for link in (r.get("links") or [])[:3]:
                title = link.get("title") or link.get("url") or ""
                url = link.get("url") or ""
                if url:
                    tavily_lines.append(f"  - [{title}]({url})")
            tavily_lines.append("")
    else:
        tavily_lines.append("_当前没有可用的 Tavily 搜索结果。_\n")

    tavily_lines.append("\n## 三、综合解读（Fast 模型总结）\n")
    if enabled and results:
        try:
            summary_text = summarize_company_from_tavily(insights)
            tavily_lines.append(summary_text or "（未能生成有效总结。）")
        except Exception as exc:
            tavily_lines.append(f"（生成综合解读时出错：{exc}）")
    else:
        tavily_lines.append("当前未实际调用 Tavily 或无有效结果，无法生成综合解读。\n")

    tavily_report_path = output_dir / "tavily_report.md"
    tavily_report_path.write_text("\n".join(tavily_lines), encoding="utf-8")

    # Matching 报告
    match_lines = [
        "# Matching 报告\n",
        f"- **JD 文件**: `{jd_path}`",
        f"- **简历文件**: `{resume_path}`\n",
    ]
    resp_matches = (matching_refined or {}).get("responsibility_semantic_matches")
    match_lines.append("## 职责语义对齐（仅数据）\n")
    if resp_matches:
        match_lines.append("| JD 职责 | 对应简历句子示例 |")
        match_lines.append("|---------|------------------|")
        for item in resp_matches:
            sentences = item.get("resume_sentences") or []
            match_lines.append(f"| {item.get('jd_item','')} | {'；'.join(sentences[:2])} |")
        match_lines.append("\n```json")
        match_lines.append(json.dumps({"responsibility_semantic_matches": resp_matches}, ensure_ascii=False, indent=2))
        match_lines.append("```")
    else:
        match_lines.append("_本次未生成职责语义对齐数据。_\n")
    req_matches = (matching_refined or {}).get("requirement_semantic_matches")
    match_lines.append("## 任职要求语义对齐（仅数据）\n")
    if req_matches:
        match_lines.append("| JD 要求 | 对应简历句子示例 |")
        match_lines.append("|---------|------------------|")
        for item in req_matches:
            sentences = item.get("resume_sentences") or []
            match_lines.append(f"| {item.get('jd_requirement','')} | {'；'.join(sentences[:2])} |")
        match_lines.append("\n```json")
        match_lines.append(json.dumps({"requirement_semantic_matches": req_matches}, ensure_ascii=False, indent=2))
        match_lines.append("```")
    else:
        match_lines.append("_本次未生成任职要求语义对齐数据。_\n")
    match_report_path = output_dir / "matching_report.md"
    match_report_path.write_text("\n".join(match_lines), encoding="utf-8")

    # 大牛辩论报告
    debate_lines = [
        "# 大牛辩论与合议报告\n",
        f"- **JD 文件**: `{jd_path}`",
        f"- **简历文件**: `{resume_path}`\n",
        "## 使用的大牛角色\n",
        ", ".join(get_enabled_personas_from_env() or ["_未配置_"]) + "\n",
        "## 各大牛个人观点\n",
    ]
    if debate_rounds:
        for idx, r in enumerate(debate_rounds, 1):
            display_name = r.get("display_name") or r.get("persona") or f"persona_{idx}"
            debate_lines.append(f"### {idx}. {display_name}\n")
            if r.get("stance"):
                debate_lines.append(f"- **本次立场**: {r['stance']}")
            debate_lines.append(f"- **结论（verdict）**: {r.get('verdict') or '（未给出）'}")
            if r.get("confidence") is not None:
                debate_lines.append(f"- **信心程度（0-1）**: {r['confidence']}")
            if r.get("analysis"):
                debate_lines.append(f"- **详细分析**:\n\n{r['analysis']}\n")
            if r.get("advice_to_candidate"):
                debate_lines.append(f"- **给候选人的建议**:\n\n{r['advice_to_candidate']}\n")
    else:
        debate_lines.append("_本次未生成任何大牛辩论结果。_\n")
    debate_lines.append("## 合议总结\n")
    if final_competitiveness:
        debate_lines.append(f"- **整体结论**: {final_competitiveness.get('overall_verdict') or '（未给出）'}\n")
        for p in (final_competitiveness.get("summary_points") or [])[:10]:
            debate_lines.append(f"  - {p}")
        if final_competitiveness.get("suggested_strategy"):
            debate_lines.append("\n- **推荐策略**:\n")
            debate_lines.append(final_competitiveness["suggested_strategy"])
    else:
        debate_lines.append("_本次未生成合议总结。_\n")
    debate_report_path = output_dir / "debate_report.md"
    debate_report_path.write_text("\n".join(debate_lines), encoding="utf-8")

    return {
        "jd": jd_report_path,
        "resume": resume_report_path,
        "tavily": tavily_report_path,
        "matching": match_report_path,
        "debate": debate_report_path,
    }


def run_once() -> None:
    """执行一次完整分析流程：input → OCR → 解析 → Tavily → 匹配 → 辩论 → 报告 → 入库。"""
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(base_dir / ".env", override=True)

    jd_path, resume_path = get_jd_and_resume_from_input(base_dir)
    if jd_path is None or resume_path is None:
        raise FileNotFoundError(
            "未找到 JD 或简历文件。可通过 .env 设置 INPUT_JD_PATH 与 INPUT_RESUME_PATH 指定路径，"
            "或将文件放入 INPUT_DIR 目录（默认 input/），文件名需含 jd/job（JD）、resume/cv（简历），支持 PDF 与图片。"
        )
    _model = os.getenv("GEMINI_MODEL") or getattr(llm_client, "_default_model", "gemini-3-flash-preview")
    print("[1/7] JD 读取与结构化（OCR + LLM 结构化）")
    print(f"      入口文件: {jd_path}")
    print(f"      调用模型: Gemini API（{_model}）— Flash OCR → LLM 解析公司/岗位等")
    jd_text, job_profile, job_json = extract_jd_structured(jd_path)
    _usage_snapshot()
    jd_preview = jd_text.replace("\n", " ")[:120] + ("…" if len(jd_text) > 120 else "")
    print(f"      JD 原文预览: {jd_preview}")
    print(f"      结构化 JD: company={job_profile.company!r}, role_title={job_profile.role_title!r}, location={job_profile.location!r}")
    print("      完成")

    print("[2/7] 简历读取与结构化（OCR + LLM 结构化）")
    print(f"      入口文件: {resume_path}")
    print(f"      调用模型: Gemini API（{_model}）— Flash OCR → LLM 解析")
    resume_text, resume_profile, resume_json = extract_resume_structured(resume_path)
    _usage_snapshot()
    resume_preview = resume_text.replace("\n", " ")[:120] + ("…" if len(resume_text) > 120 else "")
    print(f"      简历原文预览: {resume_preview}")
    print(f"      结构化简历: name={resume_profile.name!r}, headline={resume_profile.headline!r}, years_of_experience={resume_profile.years_of_experience}")
    print("      完成")

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
        print("[3/7] 使用缓存跳过 Tavily/匹配/辩论（设 FORCE_REANALYZE=true 可强制重新执行）")
        print(f"      缓存文件: {cache_path}")
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        job_profile = JobProfile.model_validate(data["job_profile"])
        job_json = data["job_json"]
        resume_profile = ResumeProfile.model_validate(data["resume_profile"])
        resume_json = data["resume_json"]
        matching_result = MatchingResult.model_validate(data["matching_result"])
        matching_refined = data["matching_refined"]
        tavily_insights = data.get("tavily_insights")
        debate_rounds = data.get("debate_rounds")
        final_competitiveness = data.get("final_competitiveness")
    else:
        print("[3/7] Tavily 搜索（公司/岗位/行业情报）")
        print(f"      输入: company={job_profile.company!r}, role_title={job_profile.role_title!r}, location={job_profile.location!r}")
        print(f"      调用: Tavily API（已配置密钥: {'是' if os.getenv('TAVILY_API_KEY') else '否'}）")
        tavily_insights = run_tavily_search(job_profile)
        print(f"      Tavily 搜索完成，查询条目数: {len((tavily_insights or {}).get('queries') or [])}, 结果条目数: {len((tavily_insights or {}).get('search_results') or [])}")
        _match_model = os.getenv("GEMINI_MODEL") or getattr(llm_client, "_default_model", "")
        print("[4/7] 匹配分析（规则维度 + 句子级语义对齐）")
        print(f"      输入概要: jd_len={len(jd_text)}, resume_len={len(resume_text)}，job_profile / resume_profile 已就绪")
        print(f"      调用模型: Gemini API（{_match_model}）— 语义对齐")
        matching_result, matching_refined = compute_matching_with_details(
            resume_profile, job_profile, jd_text=jd_text, resume_text=resume_text
        )
        _usage_snapshot()
        print(f"      匹配分析完成，维度分: {matching_result.dimensions}")
        _debate_model = os.getenv("GEMINI_MODEL") or getattr(llm_client, "_default_model", "")
        print("[5/7] 大牛辩论与合议")
        print(f"      调用模型: Gemini API（{_debate_model}）；每人随机 50% 看好/看空")
        debate_rounds = []
        for p in get_enabled_personas_from_env():
            conf = get_persona(p)
            print(f"      → 开始辩论: persona={conf.get('display_name', p) if conf else p}（id={p}）")
            result = _run_debate_for_persona(
                p, jd_text, resume_text, matching_result, matching_refined, tavily_insights
            )
            if result:
                debate_rounds.append(result)
                print(f"        辩论完成: stance={result.get('stance')}, verdict={result.get('verdict')}")
        print(f"      → 合议总结: Gemini API（{_debate_model}）")
        final_competitiveness = _summarize_debate(debate_rounds or [])
        print(f"      合议完成，总体结论: {final_competitiveness.get('overall_verdict')!r}")
        cache_data = {
            "job_profile": job_profile.model_dump(mode="json"),
            "job_json": job_json,
            "resume_profile": resume_profile.model_dump(mode="json"),
            "resume_json": resume_json,
            "matching_result": matching_result.model_dump(mode="json"),
            "matching_refined": matching_refined,
            "tavily_insights": tavily_insights,
            "debate_rounds": debate_rounds,
            "final_competitiveness": final_competitiveness,
        }
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[6/7] 生成 Markdown 报告")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = base_dir / "test_outputs" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _write_reports(
        output_dir,
        jd_path,
        resume_path,
        jd_text,
        resume_text,
        job_profile,
        resume_profile,
        tavily_insights or {},
        matching_refined,
        debate_rounds or [],
        final_competitiveness or {},
    )
    print(f"      报告目录: {output_dir}")

    print("[7/7] 收尾与入库...")
    save_to_db = (os.getenv("SAVE_TO_DB", "true") or "").strip().lower() == "true"
    if not save_to_db:
        print(f"      [提示] 当前 SAVE_TO_DB 未启用（设 true 可入库）")
    else:
        print("      正在写入数据库...")
    print("报告已生成:")
    for name, p in paths.items():
        print(f"- {name}: {p}")
    if save_to_db:
        try:
            from src.db.analysis_run_repo import save_analysis_run
            from src.db.application_repo import create_from_analysis
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
                debate_rounds=debate_rounds,
                tavily_insights=tavily_insights,
                final_competitiveness=final_competitiveness,
            )
            print(f"      已入库: job_id={job_id}, resume_id={resume_id}, analysis_id={analysis_id}, rewritten_resume_id={rewritten_id}")
            if analysis_id is not None:
                try:
                    create_from_analysis(analysis_id)
                    print(f"      已自动创建一条投递记录（applications 表，可在网页端「投递记录」Tab 查看）")
                except Exception as app_e:
                    print(f"      自动创建投递记录失败（可稍后在 Tracker 中手动添加）: {app_e}")
        except Exception as e:
            print(f"      入库失败（报告已生成）: {e}")
            traceback.print_exc()
    else:
        print("      未启用入库（SAVE_TO_DB=false）。")
