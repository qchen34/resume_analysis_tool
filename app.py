from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
import streamlit as st
import pandas as pd

from src.graph.pipeline import run_analysis
from src.models.schemas import JobProfile, ResumeProfile, MatchingResult
from src.db.analysis_run_repo import save_analysis_run
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
from src.db.application_repo import (
    create_from_analysis,
    list_applications,
    get_stats,
    to_dict,
    create as create_application,
    update as update_application,
)
from src.analysis.strategy_summary import run_strategy_summary
from tracker_review import run_tracker_review


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)
# DB 表（含 applications）在首次导入 src.db 时于 src/db/__init__.py 内初始化，命令行与网页端共用


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


def _render_tracker_tab() -> None:
    """投递记录模块：独立于分析流程，可随时使用。"""
    st.subheader("投递记录（Tracker）")
    st.caption("独立模块，与 JD/简历分析平行。可在此直接新增、查看、编辑投递，无需先做分析；若刚完成分析可勾选「使用本次分析预填」。")

    # 统计与策略
    st.caption("投递统计")
    # 默认选择「最近 30 天」
    period = st.selectbox(
        "统计周期",
        ["最近 30 天", "最近 7 天", "全部"],
        index=0,
        key="stats_period",
    )
    if period == "最近 30 天":
        to_d, from_d = datetime.utcnow(), datetime.utcnow() - timedelta(days=30)
    elif period == "最近 7 天":
        to_d, from_d = datetime.utcnow(), datetime.utcnow() - timedelta(days=7)
    else:
        from_d, to_d = None, None
    stats = get_stats(from_date=from_d, to_date=to_d)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("投递数", stats["total"])
    c2.metric("有回复", f"{stats['replied']} ({stats['reply_rate']:.1f}%)")
    c3.metric("有面试", f"{stats['interviewed']} ({stats['interview_rate']:.1f}%)")
    c4.metric("Offer", f"{stats['with_offer']} ({stats['offer_rate']:.1f}%)")

    # 选择参与本次复盘的大牛（多选）
    all_personas = list_all_persona_ids()
    default_personas = get_enabled_personas_from_env() or all_personas
    selected_personas = st.multiselect(
        "选择参与本次求职复盘的大牛（多选）",
        options=all_personas,
        default=default_personas,
        key="tracker_review_personas",
        help="不选则默认使用 .env 中配置的 DEBATE_PERSONAS；如 .env 为空则使用全部人物。",
    )

    if st.button("生成求职复盘", key="strategy_btn"):
        try:
            with st.spinner("正在进行多角色 Tracker 复盘（可能需要数十秒）…"):
                personas_arg = selected_personas or None
                summary = run_tracker_review(personas_override=personas_arg)
            st.markdown("**总结性求职策略评估与建议（来自 Tracker Review）**")
            st.markdown(summary)
            st.caption("完整的多人物详细报告与本次总结已写入 `test_outputs/tracker_<timestamp>/` 以及 `memory/memory.md`。")
        except Exception as e:
            st.error(f"生成失败：{e}")

    st.divider()
    st.caption("投递列表（可直接在表格中修改，空字段显示为 —）")
    apps = list_applications(limit=50)

    if not apps:
        st.caption("暂无投递记录。可通过命令行运行 `python main.py` 或在本页完成一次分析后点击「将本次分析记为一次投递」，也可在下方「新增投递」手动添加。")
        # 构造一行占位，仅用于展示表头
        df_empty = pd.DataFrame(
            [
                {
                    "id": 0,
                    "公司": "",
                    "职位": "",
                    "地点": "",
                    "薪资范围": "",
                    "平台": "",
                    "已投简历": None,
                    "有回复": None,
                    "有面试": None,
                    "Offer(0/1)": None,
                    "面试轮次": None,
                    "Offer": "",
                    "创建时间": "",
                }
            ]
        )
        st.data_editor(
            df_empty,
            num_rows="fixed",
            disabled=True,
            key="applications_editor_empty",
        )
    else:
        # ORM -> DataFrame
        data = []
        for a in apps:
            data.append(
                {
                    "id": a.id,
                    "公司": a.company or "",
                    "职位": a.role_title or "",
                    "地点": a.location or "",
                    "薪资范围": a.salary_range or "",
                    "平台": a.platform or "",
                    "已投简历": a.resume_sent,
                    "有回复": a.has_reply,
                    "有面试": a.has_interview,
                    "面试轮次": a.interview_rounds,
                    "Offer(0/1)": a.offer,
                    "Comments": a.offer_details or "",
                    "创建时间": a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
                }
            )
        df_original = pd.DataFrame(data)

        edited_df = st.data_editor(
            df_original,
            num_rows="fixed",
            column_config={
                "id": st.column_config.NumberColumn("id", disabled=True),
                "Offer(0/1)": st.column_config.NumberColumn("Offer(0/1)", help="0=无 Offer，1=有 Offer"),
                "创建时间": st.column_config.TextColumn("创建时间", disabled=True),
            },
            key="applications_editor",
        )

        if st.button("保存表格中所有修改", key="applications_save_all"):
            try:
                for idx, row in edited_df.iterrows():
                    original = df_original.loc[idx]
                    app_id = int(row["id"])
                    updates: Dict[str, Any] = {}

                    def _changed(col: str) -> bool:
                        o_val = original[col]
                        n_val = row[col]
                        if pd.isna(o_val) and pd.isna(n_val):
                            return False
                        return o_val != n_val

                    # 文本字段
                    for col, field in [
                        ("公司", "company"),
                        ("职位", "role_title"),
                        ("地点", "location"),
                        ("薪资范围", "salary_range"),
                        ("平台", "platform"),
                        ("Comments", "offer_details"),
                    ]:
                        if _changed(col):
                            val = str(row[col]).strip()
                            updates[field] = val or None

                    # 布尔 / 标志字段（0/1）
                    for col, field in [
                        ("已投简历", "resume_sent"),
                        ("有回复", "has_reply"),
                        ("有面试", "has_interview"),
                        ("Offer(0/1)", "offer"),
                    ]:
                        if _changed(col):
                            val = row[col]
                            if pd.isna(val):
                                updates[field] = None
                            else:
                                try:
                                    iv = int(val)
                                except (TypeError, ValueError):
                                    iv = None
                                if iv is None:
                                    updates[field] = None
                                else:
                                    updates[field] = 1 if iv != 0 else 0

                    # 面试轮次（整数或 None）
                    if _changed("面试轮次"):
                        val = row["面试轮次"]
                        if pd.isna(val) or val == "":
                            updates["interview_rounds"] = None
                        else:
                            try:
                                updates["interview_rounds"] = int(val)
                            except (TypeError, ValueError):
                                updates["interview_rounds"] = None

                    if updates:
                        update_application(app_id, **updates)

                st.success("已保存所有修改。")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"保存失败：{e}")

    with st.expander("新增投递（或从本次分析创建）"):
        use_current_analysis = st.checkbox(
            "使用本次分析结果预填（需先在「JD 与简历分析」完成一次分析并入库）",
            value=bool(st.session_state.get("last_analysis_id")),
            key="tracker_use_analysis",
        )
        platform = st.text_input("投递平台", placeholder="如 Boss、拉勾、猎聘", key="tracker_platform")
        resume_sent = st.checkbox("已投递简历", value=True, key="tracker_resume_sent")
        has_reply = st.selectbox("是否有回复", ["", "是", "否"], index=0, key="tracker_has_reply")
        has_interview = st.selectbox("是否邀约面试", ["", "是", "否"], index=0, key="tracker_has_interview")
        offer_flag = st.selectbox("是否有 Offer", ["", "是", "否"], index=0, key="tracker_has_offer")
        if st.button("保存投递记录", key="save_application"):
            try:
                if use_current_analysis and st.session_state.get("last_analysis_id"):
                    app = create_from_analysis(
                        st.session_state["last_analysis_id"],
                        platform=platform or None,
                        resume_sent=1 if resume_sent else 0,
                        has_reply={"是": 1, "否": 0}.get(has_reply) if has_reply else None,
                        has_interview={"是": 1, "否": 0}.get(has_interview) if has_interview else None,
                        offer={"是": 1, "否": 0}.get(offer_flag) if offer_flag else None,
                    )
                else:
                    app = create_application(
                        platform=platform or None,
                        resume_sent=1 if resume_sent else 0,
                        has_reply={"是": 1, "否": 0}.get(has_reply) if has_reply else None,
                        has_interview={"是": 1, "否": 0}.get(has_interview) if has_interview else None,
                        offer={"是": 1, "否": 0}.get(offer_flag) if offer_flag else None,
                    )
                if app:
                    st.success(f"已创建投递记录 id={app.id}。")
                    st.rerun()
            except Exception as e:
                st.error(f"保存失败：{e}")


def main() -> None:
    st.set_page_config(
        page_title="求职助手（JD × 简历 × 投递复盘）",
        page_icon=":page_facing_up:",
        layout="wide",
    )

    st.title("求职助手")
    st.caption("一站式：**JD × 简历匹配分析** + **投递记录 Tracker** + **多角色求职复盘**。")

    # 顶层：分析与投递记录为平行模块
    tab_analysis, tab_tracker = st.tabs(["JD 与简历分析", "投递记录 (Tracker)"])

    with st.sidebar:
        st.subheader("快速开始")
        st.markdown(
            "- **JD 与简历分析**：上传文件 → 点击「解析」→（可选）选择参与辩论的大牛 → 点击「开始分析」。\n"
            "- **投递记录**：查看统计 → 直接编辑表格并保存 → 点击「生成求职复盘」。"
        )
        st.divider()
        st.caption("隐私提示：你上传/粘贴的内容会用于模型分析，请避免提交敏感信息。")

    # ---------- Tab 1：JD 与简历分析 ----------
    with tab_analysis:
        st.caption("按照下方步骤完成一次分析。")
        # 本地运行时仍可从 input 目录自动发现文件；云端部署时通常不使用
        jd_path, resume_path = get_jd_and_resume_from_input(BASE_DIR)

        st.markdown(
            "**步骤 1：上传文件并解析**"
        )
        st.caption("上传后需点击「解析」提取文本；你也可以直接在文本框中粘贴/编辑内容。")

        if "jd_text" not in st.session_state:
            st.session_state.jd_text = ""
        if "resume_text" not in st.session_state:
            st.session_state.resume_text = ""

        col_upload_jd, col_upload_resume = st.columns(2)
        with col_upload_jd:
            upload_jd = st.file_uploader(
                "上传职位描述（Job Description，可拖拽）",
                type=["pdf", "png", "jpg", "jpeg", "bmp", "tiff"],
                key="upload_jd",
                help="支持 PDF、图片；上传后需点击「解析 JD」才会提取文本。",
            )
            parse_jd_clicked = st.button(
                "解析职位描述（Job Description）", key="parse_jd", use_container_width=True
            )
            if parse_jd_clicked:
                if upload_jd is not None:
                    with st.spinner("正在从上传的职位描述提取文本…"):
                        try:
                            st.session_state.jd_text = _extract_text_from_upload(upload_jd)
                        except Exception as e:
                            st.error(f"职位描述解析失败: {e}")
                            st.session_state.jd_text = ""
                elif jd_path and jd_path.exists():
                    with st.spinner("正在提取职位描述文本…"):
                        try:
                            st.session_state.jd_text = extract_text_auto(jd_path)
                        except Exception as e:
                            st.error(f"职位描述解析失败: {e}")
                            st.session_state.jd_text = ""
                else:
                    st.warning("请先上传职位描述文件，或直接在下方文本框粘贴职位描述内容。")

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
                    st.warning("请先上传简历文件，或直接在下方文本框粘贴简历内容。")

        # 文本框与 session_state 绑定（key 与 jd_text/resume_text 一致），解析结果写入 session 后会自动显示
        col_jd, col_resume = st.columns(2)
        with col_jd:
            jd_text = st.text_area(
                "职位描述（Job Description）原文（可编辑）",
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

        st.divider()
        st.markdown("**步骤 2：选择分析大牛（可多选）**")
        st.caption("勾选越多，观点越丰富；也会更耗时。")

        _all_ids = list_all_persona_ids()
        _default_ids = get_enabled_personas_from_env()
        _default = (
            [p for p in _default_ids if p in _all_ids]
            if _default_ids
            else (_all_ids[:3] if _all_ids else [])
        )

        def _persona_label(pid: str) -> str:
            c = get_persona(pid)
            if not c:
                return pid
            name = c.get("display_name", pid)
            cat = c.get("category", "")
            return f"{name}（{cat}）" if cat else str(name)

        # 用 checkbox 代替 multiselect：视觉上直接列出所有大牛
        selected_personas: list[str] = []
        personas_by_cat = get_personas_by_category()
        if not personas_by_cat:
            personas_by_cat = {"全部": _all_ids}

        # 初始化默认勾选（仅第一次）
        if "tracker_debate_persona_init" not in st.session_state:
            st.session_state.tracker_debate_persona_init = True
            for pid in _default:
                st.session_state[f"persona_checked_{pid}"] = True

        for cat, pids in personas_by_cat.items():
            if not pids:
                continue
            with st.expander(f"{cat}（{len(pids)}）", expanded=(cat in {"商界", "政界"})):
                cols = st.columns(3)
                for i, pid in enumerate(pids):
                    with cols[i % 3]:
                        checked = st.checkbox(
                            _persona_label(pid),
                            value=bool(st.session_state.get(f"persona_checked_{pid}", False)),
                            key=f"persona_checked_{pid}",
                        )
                        if checked:
                            selected_personas.append(pid)

        personas_to_use = selected_personas if selected_personas else _default

        st.divider()
        st.markdown("**步骤 3：开始分析**")
        default_save_to_db = os.getenv("SAVE_TO_DB", "true").strip().lower() == "true"
        default_force_re = os.getenv("FORCE_REANALYZE", "false").strip().lower() == "true"
        with st.expander("高级选项", expanded=False):
            save_to_db_ui = st.checkbox(
                "保存结果到数据库",
                value=default_save_to_db,
                key="ui_save_to_db",
                help="开启后，本次分析结果会写入本项目数据库，便于在 Tracker 中复用。",
            )
            force_reanalyze_ui = st.checkbox(
                "强制重新分析（忽略缓存）",
                value=default_force_re,
                key="ui_force_reanalyze",
                help="开启后，即使存在缓存也会重新跑完整流程。",
            )

        run_clicked = st.button("开始分析", type="primary", use_container_width=True)

        if run_clicked:
            if not jd_text.strip() or not resume_text.strip():
                st.warning("请先通过 input 目录、上传文件或文本框提供 JD 与简历内容。")
            elif not personas_to_use:
                st.warning("请至少选择一位大牛参与辩论。")
            else:
                with st.spinner("正在解析与匹配，请稍候..."):
                    jd_clean = jd_text.strip()
                    resume_clean = resume_text.strip()
                    # run 级缓存：与 CLI 共享 test_outputs/cache/<hash>.json
                    cache_root = BASE_DIR / "test_outputs" / "cache"
                    cache_root.mkdir(parents=True, exist_ok=True)
                    key_str = jd_clean + "\n====RESUME====\n" + resume_clean
                    cache_key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
                    cache_path = cache_root / f"{cache_key}.json"

                    state: Dict[str, Any]

                    try:
                        if (not force_reanalyze_ui) and cache_path.exists():
                            # 使用缓存跳过 Tavily/匹配/辩论
                            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
                            job_profile = JobProfile.model_validate(cache_data["job_profile"])
                            resume_profile = ResumeProfile.model_validate(cache_data["resume_profile"])
                            matching_result = MatchingResult.model_validate(cache_data["matching_result"])
                            matching_refined = cache_data.get("matching_refined") or {}
                            tavily_insights = cache_data.get("tavily_insights") or {}
                            debate_rounds = cache_data.get("debate_rounds") or []
                            final_competitiveness = cache_data.get("final_competitiveness") or {}
                            state = {
                                "job_profile": job_profile,
                                "resume_profile": resume_profile,
                                "matching_result": matching_result,
                                "matching_refined": matching_refined,
                                "tavily_insights": tavily_insights,
                                "debate_rounds": debate_rounds,
                                "final_competitiveness": final_competitiveness,
                            }
                        else:
                            # 正常跑一次 LangGraph 分析
                            state = _run_analysis(
                                jd_clean,
                                resume_clean,
                                debate_personas_override=personas_to_use,
                            )
                            # 写入 run 级缓存，便于下次复用
                            try:
                                job_profile = state.get("job_profile")
                                resume_profile = state.get("resume_profile")
                                matching_result = state.get("matching_result")
                                cache_payload = {
                                    "job_profile": job_profile.model_dump(mode="json") if isinstance(job_profile, JobProfile) else {},
                                    "job_json": {},  # Web 端暂不区分 job_json/raw_json
                                    "resume_profile": resume_profile.model_dump(mode="json") if isinstance(resume_profile, ResumeProfile) else {},
                                    "resume_json": {},
                                    "matching_result": matching_result.model_dump(mode="json") if isinstance(matching_result, MatchingResult) else {},
                                    "matching_refined": state.get("matching_refined") or {},
                                    "tavily_insights": state.get("tavily_insights"),
                                    "debate_rounds": state.get("debate_rounds"),
                                    "final_competitiveness": state.get("final_competitiveness"),
                                }
                                cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                            except Exception:
                                pass

                    except Exception as exc:
                        st.error(f"分析过程中出现错误：{exc}")
                    else:
                        st.session_state.analysis_done = True
                        st.session_state.state = state
                        st.session_state.jd_text_saved = jd_clean
                        st.session_state.resume_text_saved = resume_clean
                        # 是否入库由前端勾选控制
                        if save_to_db_ui and state.get("job_profile") and state.get("resume_profile") and state.get("matching_result"):
                            try:
                                _jid, _rid, aid, _ = save_analysis_run(
                                    jd_text=jd_clean,
                                    resume_text=resume_clean,
                                    job_profile=state["job_profile"],
                                    resume_profile=state["resume_profile"],
                                    matching_result=state["matching_result"],
                                    matching_refined=state.get("matching_refined"),
                                    debate_rounds=state.get("debate_rounds"),
                                    tavily_insights=state.get("tavily_insights"),
                                    final_competitiveness=state.get("final_competitiveness"),
                                )
                                st.session_state.last_analysis_id = aid
                                try:
                                    create_from_analysis(aid)
                                except Exception:
                                    pass
                            except Exception:
                                st.session_state.last_analysis_id = None
                        else:
                            st.session_state.last_analysis_id = None

        if not st.session_state.get("analysis_done"):
            st.info("填写 JD/简历、**选择大牛** 后点击 **开始分析**。分析完成后可在此查看结果，或到顶部「投递记录」将本次分析记为一次投递。")
        else:
            state = st.session_state.state
            jd_text_saved = st.session_state.get("jd_text_saved") or st.session_state.get("jd_text") or ""
            resume_text_saved = st.session_state.get("resume_text_saved") or st.session_state.get("resume_text") or ""

            job_profile = state.get("job_profile")
            resume_profile = state.get("resume_profile")
            matching_result = state.get("matching_result")
            matching_refined = state.get("matching_refined") or {}
            tavily_insights = state.get("tavily_insights") or {}
            debate_rounds = state.get("debate_rounds") or []
            final_competitiveness = state.get("final_competitiveness") or {}

            # 分析结果子 Tab（不含投递记录，投递为独立模块）
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

                # 分析 → 投递入口：将本次分析记为一次投递（投递记录为独立模块，请到顶部「投递记录」查看）
                st.divider()
                st.caption("投递复盘")
                last_aid = st.session_state.get("last_analysis_id")
                if last_aid is not None:
                    if st.button("将本次分析记为一次投递", key="mark_as_application"):
                        try:
                            app = create_from_analysis(last_aid)
                            if app:
                                st.success(f"已创建投递记录（公司：{app.company or '—'}，职位：{app.role_title or '—'}）。请点击顶部 **投递记录 (Tracker)** 查看与编辑。")
                            else:
                                st.warning("未找到对应分析记录，无法创建投递。")
                        except Exception as e:
                            st.error(f"创建投递记录失败：{e}")
                else:
                    st.caption("本次分析未入库，无法关联投递记录；请确保 SAVE_TO_DB 已启用。")

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

    # ---------- Tab 2：投递记录（独立模块，平行于分析） ----------
    with tab_tracker:
        _render_tracker_tab()


if __name__ == "__main__":
    main()

