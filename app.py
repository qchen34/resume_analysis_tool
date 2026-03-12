from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import os

from dotenv import load_dotenv
import streamlit as st

from src.graph.pipeline import run_analysis
from src.models.schemas import JobProfile, ResumeProfile, MatchingResult


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


def _run_analysis(jd_text: str, resume_text: str) -> Dict[str, Any]:
    """
    通过 LangGraph 执行完整分析：解析 JD/简历 → Tavily 搜索 → 规则匹配 + 语义对齐 → 大牛辩论与合议。

    直接返回最终的 graph state（AnalysisState 字典）。
    """
    state = run_analysis(jd_text, resume_text)
    return dict(state)


def main() -> None:
    st.set_page_config(
        page_title="Resume & JD Analysis",
        page_icon=":page_facing_up:",
        layout="wide",
    )

    st.title("Resume Analysis Tool (Web)")
    st.markdown(
        "输入目标 JD 与简历原文，一键完成：规则匹配、句子级语义对齐、Tavily 情报，以及多位大牛的辩论与合议。"
        " 当前网页直接复用后端模块，不存储任何输入内容。"
    )

    with st.sidebar:
        st.header("设置")
        st.markdown("- 使用 `.env` 中的 Gemini / Tavily / 大牛角色（DEBATE_PERSONAS）配置。")

    col_jd, col_resume = st.columns(2)
    with col_jd:
        jd_text = st.text_area(
            "JD 原文",
            height=260,
            placeholder="在这里粘贴 JD 文本...",
        )
    with col_resume:
        resume_text = st.text_area(
            "简历原文",
            height=260,
            placeholder="在这里粘贴简历文本...",
        )

    if "analysis_done" not in st.session_state:
        st.session_state.analysis_done = False
    if "jd_text" not in st.session_state:
        st.session_state.jd_text = ""
    if "resume_text" not in st.session_state:
        st.session_state.resume_text = ""

    run_clicked = st.button("开始分析", type="primary", use_container_width=True)

    if run_clicked:
        if not jd_text.strip() or not resume_text.strip():
            st.warning("请先同时填写 JD 与简历文本。")
        else:
            with st.spinner("正在解析与匹配，请稍候..."):
                try:
                    state = _run_analysis(jd_text.strip(), resume_text.strip())
                except Exception as exc:
                    st.error(f"分析过程中出现错误：{exc}")
                else:
                    st.session_state.analysis_done = True
                    st.session_state.state = state
                    st.session_state.jd_text = jd_text.strip()
                    st.session_state.resume_text = resume_text.strip()

    if not st.session_state.get("analysis_done"):
        st.info("填写 JD 与简历后点击 **开始分析**，结果会显示在这里。")
        return

    state: Dict[str, Any] = st.session_state.state
    jd_text_saved: str = st.session_state.jd_text
    resume_text_saved: str = st.session_state.resume_text

    job_profile: JobProfile | None = state.get("job_profile")
    resume_profile: ResumeProfile | None = state.get("resume_profile")
    matching_result: MatchingResult | None = state.get("matching_result")
    matching_refined: Dict[str, Any] = state.get("matching_refined") or {}
    tavily_insights: Dict[str, Any] = state.get("tavily_insights") or {}
    debate_rounds: list[Dict[str, Any]] = state.get("debate_rounds") or []
    final_competitiveness: Dict[str, Any] = state.get("final_competitiveness") or {}

    st.markdown("---")
    st.header("Step 2：原文与基础信息")

    col_jd_sum, col_resume_sum = st.columns(2)
    with col_jd_sum:
        st.subheader("JD 原文")
        if job_profile:
            st.write(f"**岗位**：{job_profile.role_title or '未提供'}")
            st.write(f"**公司**：{job_profile.company or '未提供'}")
            st.write(f"**地点**：{job_profile.location or '未提供'}")
        st.text_area("JD 文本（只读）", jd_text_saved, height=260, disabled=True)

    with col_resume_sum:
        st.subheader("简历原文")
        st.text_area("简历文本（只读）", resume_text_saved, height=260, disabled=True)

    st.markdown("---")
    st.header("Step 3：Tavily 公司 / 岗位 / 行业情报")

    enabled = bool(tavily_insights.get("enabled"))
    queries = tavily_insights.get("queries") or []
    results = tavily_insights.get("search_results") or []

    st.subheader("搜索策略（我们是如何从 JD 构造查询）")
    if not tavily_insights:
        st.write("本次未生成 Tavily 搜索计划。")
    else:
        if not enabled:
            reason = tavily_insights.get("reason") or "Tavily 未启用。"
            st.info(f"当前未实际调用 Tavily：{reason}")
        if queries:
            st.markdown("**查询明细：**")
            for q in queries:
                st.markdown(
                    f"- 类型：`{q.get('type', '')}`，关键词：`{q.get('keywords', '')}`，Query：`{q.get('query', '')}`"
                )
        else:
            st.write("未生成任何查询（可能是 JD 中缺少公司 / 岗位等关键信息）。")

    st.subheader("搜索结果摘要")
    if enabled and results:
        for idx, r in enumerate(results, 1):
            st.markdown(f"**{idx}. [{r.get('type','')}] {r.get('query','')}**")
            if r.get("error"):
                st.write(f"状态：调用失败（{r['error']}）")
                continue
            if r.get("summary"):
                st.write(r["summary"])
            links = r.get("links") or []
            if links:
                st.write("参考链接（Top 3）：")
                for link in links:
                    title = link.get("title") or link.get("url") or ""
                    url = link.get("url") or ""
                    if url:
                        st.markdown(f"- [{title}]({url})")
    else:
        st.write("当前没有可用的 Tavily 搜索结果。")

    st.markdown("---")
    st.header("Step 4：Matching（规则 + 句子级语义对齐，仅数据）")

    if matching_result is None:
        st.write("本次未生成匹配结果。")
    else:
        dims = matching_result.dimensions
        st.subheader("规则层维度得分（原始数据）")
        dim_cols = st.columns(4)
        dim_cols[0].metric("技能匹配", f"{dims.skills:.2f}")
        dim_cols[1].metric("经验匹配", f"{dims.experience:.2f}")
        dim_cols[2].metric("领域契合", f"{dims.domain:.2f}")
        dim_cols[3].metric("教育达标", f"{dims.education:.2f}")
        dim_cols2 = st.columns(4)
        dim_cols2[0].metric("软能力信号", f"{dims.soft_skills_signal:.2f}")
        dim_cols2[1].metric("领导力", f"{dims.leadership:.2f}")
        dim_cols2[2].metric("沟通协作", f"{dims.communication:.2f}")
        dim_cols2[3].metric("文化契合", f"{dims.culture_fit:.2f}")

        st.subheader("差距列表（Gaps，原始数据）")
        if matching_result.gaps:
            for g in matching_result.gaps:
                st.markdown(f"- **[{g.severity}] {g.type}**：{g.name}")
                if g.detail:
                    st.write(g.detail)
        else:
            st.write("本次未生成差距列表。")

    st.subheader("句子级语义对齐（仅数据，不做评分）")
    col_resp, col_req = st.columns(2)
    with col_resp:
        st.markdown("**JD 职责语义对齐**")
        resp_matches = matching_refined.get("responsibility_semantic_matches") or []
        if resp_matches:
            for item in resp_matches:
                st.markdown(f"- JD 职责：{item.get('jd_item', '')}")
                sentences = item.get("resume_sentences") or []
                if sentences:
                    st.write("  对应简历句子示例：")
                    for s in sentences[:3]:
                        st.write(f"  - {s}")
        else:
            st.write("本次未生成职责语义对齐数据。")

    with col_req:
        st.markdown("**任职要求语义对齐**")
        req_matches = matching_refined.get("requirement_semantic_matches") or []
        if req_matches:
            for item in req_matches:
                st.markdown(f"- JD 要求：{item.get('jd_requirement', '')}")
                sentences = item.get("resume_sentences") or []
                if sentences:
                    st.write("  对应简历句子示例：")
                    for s in sentences[:3]:
                        st.write(f"  - {s}")
        else:
            st.write("本次未生成任职要求语义对齐数据。")

    st.markdown("---")
    st.header("Step 5：大牛辩论与合议（接地气视角）")

    personas_raw = os.getenv("DEBATE_PERSONAS", "wangchuan,naval,trump")
    personas = [p.strip() for p in personas_raw.split(",") if p.strip()]
    st.subheader("使用的大牛角色")
    if personas:
        st.write(", ".join(personas))
    else:
        st.write("当前未配置任何大牛角色（DEBATE_PERSONAS 为空）。")

    st.subheader("各大牛个人观点")
    if debate_rounds:
        for idx, r in enumerate(debate_rounds, 1):
            display_name = r.get("display_name") or r.get("persona") or f"persona_{idx}"
            verdict = r.get("verdict") or "（未给出结论）"
            confidence = r.get("confidence")
            analysis = r.get("analysis") or ""
            advice = r.get("advice_to_candidate") or ""

            with st.expander(f"{idx}. {display_name} —— 结论：{verdict}"):
                if confidence is not None:
                    st.write(f"信心程度（0-1）：{confidence}")
                if analysis:
                    st.markdown("**详细分析**")
                    st.write(analysis)
                if advice:
                    st.markdown("**给候选人的建议**")
                    st.write(advice)
    else:
        st.write("本次未生成任何大牛辩论结果。")

    st.subheader("合议总结")
    if final_competitiveness:
        overall = final_competitiveness.get("overall_verdict") or "（未给出整体结论）"
        st.markdown(f"**整体结论**：{overall}")

        points = final_competitiveness.get("summary_points") or []
        if isinstance(points, list) and points:
            st.markdown("**总结要点**")
            for p in points:
                st.write(f"- {p}")

        strategy = final_competitiveness.get("suggested_strategy") or ""
        if strategy:
            st.markdown("**推荐策略**")
            st.write(strategy)
    else:
        st.write("本次未生成合议总结。")


if __name__ == "__main__":
    main()

