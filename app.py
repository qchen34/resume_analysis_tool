from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

import os

from dotenv import load_dotenv
import streamlit as st

from src.graph.pipeline import run_analysis
from src.models.schemas import JobProfile, ResumeProfile, MatchingResult
from src.ocr.ocr_utils import (
    extract_text_auto,
    get_jd_and_resume_from_input,
    get_input_dir,
)
from src.llm.debate_personas import (
    get_enabled_personas_from_env,
    get_personas_by_category,
    get_persona,
    list_all_persona_ids,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


def _run_analysis(
    jd_text: str,
    resume_text: str,
    debate_personas_override: list[str] | None = None,
) -> Dict[str, Any]:
    """
    通过 LangGraph 执行完整分析。debate_personas_override 为前端选择的大牛 id 列表，非空时优先于 .env。
    """
    state = run_analysis(jd_text, resume_text, debate_personas_override=debate_personas_override)
    return dict(state)


def _extract_text_from_upload(uploaded_file) -> str:
    """将上传的文件写入临时文件后调用 OCR 提取文本。"""
    if uploaded_file is None:
        return ""
    suffix = Path(uploaded_file.name).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = Path(tmp.name)
    try:
        return extract_text_auto(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> None:
    st.set_page_config(
        page_title="Resume & JD Analysis",
        page_icon=":page_facing_up:",
        layout="wide",
    )

    st.title("Resume Analysis Tool (Web)")
    st.markdown(
        "默认使用 **input** 目录中的 JD/简历文件；也可在下方上传文件（支持拖拽）。"
        " 一键完成：规则匹配、句子级语义对齐、Tavily 情报与大牛辩论合议。"
    )

    with st.sidebar:
        st.header("开始分析步骤")
        st.markdown("1. **准备文件**：将 JD、简历放入项目根目录下的 `input/`（文件名含 jd/job、resume/cv），或在本页上传。")
        st.markdown("2. **解析**：点击「解析 JD」「解析简历」，等待 OCR 提取文本并出现在下方框内。")
        st.markdown("3. **选择大牛**：在「选择大牛」中多选参与辩论的角色（未选时使用 .env 的 DEBATE_PERSONAS）。")
        st.markdown("4. **开始分析**：点击「开始分析」，等待匹配与辩论完成。")
        st.caption("input 目录位置")
        st.code("<项目根目录>/input\n# 例如: .../resume_analysis_tool/input", language="text")

    # 仅发现 input 目录中的文件路径，不自动解析；解析由下方「解析」按钮触发
    jd_path, resume_path = get_jd_and_resume_from_input(BASE_DIR)

    st.subheader("输入方式")
    st.caption("上传 JD/简历（或使用 input 目录中的文件）后，点击「解析」按钮进行 OCR，解析结果会出现在下方文本框，再点击「开始分析」。")

    if "jd_text" not in st.session_state:
        st.session_state.jd_text = ""
    if "resume_text" not in st.session_state:
        st.session_state.resume_text = ""

    col_upload_jd, col_upload_resume = st.columns(2)
    with col_upload_jd:
        upload_jd = st.file_uploader(
            "上传 JD（可拖拽）",
            type=["pdf", "png", "jpg", "jpeg", "bmp", "tiff"],
            key="upload_jd",
            help="支持 PDF、图片；上传后需点击「解析 JD」才会提取文本。",
        )
        parse_jd_clicked = st.button("解析 JD", key="parse_jd", use_container_width=True)
        if parse_jd_clicked:
            if upload_jd is not None:
                with st.spinner("正在从上传的 JD 提取文本…"):
                    try:
                        st.session_state.jd_text = _extract_text_from_upload(upload_jd)
                    except Exception as e:
                        st.error(f"JD 解析失败: {e}")
                        st.session_state.jd_text = ""
            elif jd_path and jd_path.exists():
                with st.spinner("正在从 input 目录的 JD 提取文本…"):
                    try:
                        st.session_state.jd_text = extract_text_auto(jd_path)
                    except Exception as e:
                        st.error(f"JD 解析失败: {e}")
                        st.session_state.jd_text = ""
            else:
                st.warning("请先上传 JD 文件，或将含 jd/job 的文件放入 input 目录。")

    with col_upload_resume:
        upload_resume = st.file_uploader(
            "上传简历（可拖拽）",
            type=["pdf", "png", "jpg", "jpeg", "bmp", "tiff"],
            key="upload_resume",
            help="支持 PDF、图片；上传后需点击「解析简历」才会提取文本。",
        )
        parse_resume_clicked = st.button("解析简历", key="parse_resume", use_container_width=True)
        if parse_resume_clicked:
            if upload_resume is not None:
                with st.spinner("正在从上传的简历提取文本…"):
                    try:
                        st.session_state.resume_text = _extract_text_from_upload(upload_resume)
                    except Exception as e:
                        st.error(f"简历解析失败: {e}")
                        st.session_state.resume_text = ""
            elif resume_path and resume_path.exists():
                with st.spinner("正在从 input 目录的简历提取文本…"):
                    try:
                        st.session_state.resume_text = extract_text_auto(resume_path)
                    except Exception as e:
                        st.error(f"简历解析失败: {e}")
                        st.session_state.resume_text = ""
            else:
                st.warning("请先上传简历文件，或将含 resume/cv 的文件放入 input 目录。")

    # 文本框与 session_state 绑定（key 与 jd_text/resume_text 一致），解析结果写入 session 后会自动显示
    col_jd, col_resume = st.columns(2)
    with col_jd:
        jd_text = st.text_area(
            "JD 原文（可编辑）",
            value=st.session_state.jd_text,
            height=260,
            placeholder="将使用 input 目录或上传文件提取的文本；也可直接粘贴或修改。",
            key="jd_text",
        )
    with col_resume:
        resume_text = st.text_area(
            "简历原文（可编辑）",
            value=st.session_state.resume_text,
            height=260,
            placeholder="将使用 input 目录或上传文件提取的文本；也可直接粘贴或修改。",
            key="resume_text",
        )

    if "analysis_done" not in st.session_state:
        st.session_state.analysis_done = False

    st.subheader("选择大牛")
    st.caption("至少选一位大牛参与辩论；未选时使用 .env 中 DEBATE_PERSONAS。每人本次随机 50% 看好/看空。")
    _all_ids = list_all_persona_ids()
    _default_ids = get_enabled_personas_from_env()
    # 默认选中与 env 一致，且只保留当前人物库里存在的 id
    _default = [p for p in _default_ids if p in _all_ids] if _default_ids else (_all_ids[:3] if _all_ids else [])

    def _persona_label(pid: str) -> str:
        c = get_persona(pid)
        if not c:
            return pid
        return f"{c.get('display_name', pid)} [{c.get('category', '')}]"

    selected_personas = st.multiselect(
        "大牛（多选）",
        options=_all_ids,
        default=_default,
        format_func=_persona_label,
        key="select_debate_personas",
    )
    personas_to_use = selected_personas if selected_personas else _default

    run_clicked = st.button("开始分析", type="primary", use_container_width=True)

    if run_clicked:
        if not jd_text.strip() or not resume_text.strip():
            st.warning("请先通过 input 目录、上传文件或文本框提供 JD 与简历内容。")
        elif not personas_to_use:
            st.warning("请至少选择一位大牛参与辩论。")
        else:
            with st.spinner("正在解析与匹配，请稍候..."):
                try:
                    state = _run_analysis(
                        jd_text.strip(),
                        resume_text.strip(),
                        debate_personas_override=personas_to_use,
                    )
                except Exception as exc:
                    st.error(f"分析过程中出现错误：{exc}")
                else:
                    st.session_state.analysis_done = True
                    st.session_state.state = state
                    st.session_state.jd_text_saved = jd_text.strip()
                    st.session_state.resume_text_saved = resume_text.strip()

    if not st.session_state.get("analysis_done"):
        st.info("填写 JD/简历、**选择大牛** 后点击 **开始分析**。")
        return

    state: Dict[str, Any] = st.session_state.state
    jd_text_saved: str = st.session_state.get("jd_text_saved") or st.session_state.get("jd_text") or ""
    resume_text_saved: str = st.session_state.get("resume_text_saved") or st.session_state.get("resume_text") or ""

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
                        "立场": r.get("stance") or "—",
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

        personas = get_enabled_personas_from_env()
        st.caption("使用的大牛角色（每人本次随机看好/看空）")
        if personas:
            st.write(", ".join(personas))
        else:
            st.write("当前未配置任何大牛角色（DEBATE_PERSONAS 为空）。")

        st.markdown("**各大牛个人观点**")
        if debate_rounds:
            for idx, r in enumerate(debate_rounds, 1):
                display_name = r.get("display_name") or r.get("persona") or f"persona_{idx}"
                verdict = r.get("verdict") or "（未给出结论）"
                stance = r.get("stance")
                confidence = r.get("confidence")
                analysis = r.get("analysis") or ""
                advice = r.get("advice_to_candidate") or ""
                title = f"{idx}. {display_name} —— {verdict}"
                if stance:
                    title += f"（立场：{stance}）"

                with st.expander(title):
                    if stance:
                        st.write(f"本次立场：{stance}")
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

