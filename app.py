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

    # 顶部概览 Tab
    tab_overview, tab_tavily, tab_matching, tab_debate = st.tabs(
        ["概览", "Tavily 情报", "匹配数据", "大牛辩论"]
    )

    # 概览：给“老板视图”，只看最关键结论
    with tab_overview:
        st.subheader("一页概览")

        col_left, col_right = st.columns(2)
        with col_left:
            st.caption("职位 & 公司")
            if job_profile:
                st.write(f"**岗位**：{job_profile.role_title or '未提供'}")
                st.write(f"**公司**：{job_profile.company or '未提供'}")
                st.write(f"**地点**：{job_profile.location or '未提供'}")
            else:
                st.write("未能解析出 JobProfile。")

            st.caption("候选人简历（原文预览）")
            st.text_area(
                "简历文本（只读，前 20 行）",
                "\n".join(resume_text_saved.splitlines()[:20]),
                height=200,
                disabled=True,
            )

        with col_right:
            st.caption("合议总结（如果已生成）")
            if final_competitiveness:
                overall = final_competitiveness.get("overall_verdict") or "（未给出整体结论）"
                st.markdown(f"**整体结论**：{overall}")

                points = final_competitiveness.get("summary_points") or []
                if isinstance(points, list) and points:
                    st.write("总结要点：")
                    for p in points[:5]:
                        st.write(f"- {p}")

                strategy = final_competitiveness.get("suggested_strategy") or ""
                if strategy:
                    st.write("推荐策略：")
                    st.write(strategy)
            else:
                st.write("本次尚未生成合议总结。")

            st.caption("大牛观点快照")
            if debate_rounds:
                snapshot = [
                    {
                        "大牛": r.get("display_name") or r.get("persona") or f"{idx+1}",
                        "结论": r.get("verdict") or "",
                        "信心": r.get("confidence"),
                    }
                    for idx, r in enumerate(debate_rounds)
                ]
                st.table(snapshot)
            else:
                st.write("本次未生成任何大牛辩论结果。")

    # Tavily 情报 Tab
    with tab_tavily:
        st.subheader("Tavily 公司 / 岗位 / 行业情报")

        enabled = bool(tavily_insights.get("enabled"))
        queries = tavily_insights.get("queries") or []
        results = tavily_insights.get("search_results") or []

        st.caption("我们如何从 JD 构造查询")
        if not tavily_insights:
            st.write("本次未生成 Tavily 搜索计划。")
        else:
            if not enabled:
                reason = tavily_insights.get("reason") or "Tavily 未启用。"
                st.info(f"当前未实际调用 Tavily：{reason}")
            if queries:
                table_data = [
                    {
                        "类型": q.get("type", ""),
                        "关键词": q.get("keywords", ""),
                        "Query": q.get("query", ""),
                    }
                    for q in queries
                ]
                st.table(table_data)
            else:
                st.write("未生成任何查询（可能是 JD 中缺少公司 / 岗位等关键信息）。")

        st.caption("搜索结果摘要")
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

    # 匹配数据 Tab
    with tab_matching:
        st.subheader("匹配数据（规则 + 句子级语义对齐，仅数据）")
        st.caption("本页仅展示原始数据，不直接给出“适不适合”结论。")

        if matching_result is None:
            st.write("本次未生成匹配结果。")
        else:
            dims = matching_result.dimensions
            st.markdown("**规则层维度得分**")
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

            st.markdown("**差距列表（Gaps）**")
            if matching_result.gaps:
                gap_table = [
                    {
                        "类型": g.type,
                        "名称": g.name,
                        "严重程度": g.severity,
                        "说明": g.detail or "",
                    }
                    for g in matching_result.gaps
                ]
                st.table(gap_table)
            else:
                st.write("本次未生成差距列表。")

        st.markdown("**句子级语义对齐（仅数据，不做评分）**")
        col_resp, col_req = st.columns(2)

        with col_resp:
            st.caption("JD 职责语义对齐")
            resp_matches = matching_refined.get("responsibility_semantic_matches") or []
            if resp_matches:
                resp_table = []
                for item in resp_matches:
                    jd_item = item.get("jd_item", "")
                    sentences = item.get("resume_sentences") or []
                    preview = "；".join(sentences[:3])
                    resp_table.append({"JD 职责": jd_item, "匹配的简历句子示例": preview})
                st.table(resp_table)
            else:
                st.write("本次未生成职责语义对齐数据。")

        with col_req:
            st.caption("任职要求语义对齐")
            req_matches = matching_refined.get("requirement_semantic_matches") or []
            if req_matches:
                req_table = []
                for item in req_matches:
                    jd_req = item.get("jd_requirement", "")
                    sentences = item.get("resume_sentences") or []
                    preview = "；".join(sentences[:3])
                    req_table.append({"JD 要求": jd_req, "匹配的简历句子示例": preview})
                st.table(req_table)
            else:
                st.write("本次未生成任职要求语义对齐数据。")

    # 大牛辩论 Tab
    with tab_debate:
        st.subheader("大牛辩论与合议（接地气视角）")

        personas_raw = os.getenv("DEBATE_PERSONAS", "wangchuan,naval,trump")
        personas = [p.strip() for p in personas_raw.split(",") if p.strip()]
        st.caption("使用的大牛角色")
        if personas:
            st.write(", ".join(personas))
        else:
            st.write("当前未配置任何大牛角色（DEBATE_PERSONAS 为空）。")

        st.markdown("**各大牛个人观点**")
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

        st.markdown("**合议总结**")
        if final_competitiveness:
            overall = final_competitiveness.get("overall_verdict") or "（未给出整体结论）"
            st.markdown(f"**整体结论**：{overall}")

            points = final_competitiveness.get("summary_points") or []
            if isinstance(points, list) and points:
                st.write("总结要点：")
                for p in points:
                    st.write(f"- {p}")

            strategy = final_competitiveness.get("suggested_strategy") or ""
            if strategy:
                st.write("推荐策略：")
                st.write(strategy)
        else:
            st.write("本次未生成合议总结。")


if __name__ == "__main__":
    main()

