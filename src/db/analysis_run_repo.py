"""
一次「JD + 简历 → 匹配 + 重写」分析运行的结果入库。
顺序：Job → Resume → Analysis → RewrittenResume（若有）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.db.base import SessionLocal
from src.db.models import Analysis, Job, Resume, RewrittenResume
from src.models.schemas import (
    JobProfile,
    MatchingResult,
    ResumeProfile,
    RewriteResult,
)


def _matching_result_to_json(
    matching_result: MatchingResult,
    matching_refined: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    将 MatchingResult 与可选的 refined 补充字段（如 responsibility_coverage、skill_coverage）合并为可入库的 JSON。
    """
    data = matching_result.model_dump(mode="json")
    if matching_refined:
        for key in ("responsibility_coverage", "skill_coverage"):
            if key in matching_refined and matching_refined[key] is not None:
                data[key] = matching_refined[key]
    return data


def save_analysis_run(
    jd_text: str,
    resume_text: str,
    job_profile: JobProfile,
    resume_profile: ResumeProfile,
    matching_result: MatchingResult,
    matching_refined: Optional[Dict[str, Any]] = None,
    rewrite_result: Optional[RewriteResult] = None,
    user_id: Optional[int] = None,
    llm_model: Optional[str] = None,
) -> Tuple[int, int, int, Optional[int]]:
    """
    将一次分析运行的完整结果写入数据库。

    顺序：插入 Job → 插入 Resume → 插入 Analysis → 若提供 rewrite_result 则插入 RewrittenResume。

    返回:
        (job_id, resume_id, analysis_id, rewritten_resume_id)
        rewritten_resume_id 在未做重写时为 None。
    """
    db = SessionLocal()
    try:
        job = Job(
            title=job_profile.role_title,
            company=job_profile.company,
            level=job_profile.level,
            location=job_profile.location,
            raw_jd_text=jd_text,
            job_profile_json=job_profile.model_dump(mode="json"),
        )
        db.add(job)
        db.flush()

        resume = Resume(
            user_id=user_id,
            raw_resume_text=resume_text,
            resume_profile_json=resume_profile.model_dump(mode="json"),
        )
        db.add(resume)
        db.flush()

        overall_score = None
        if matching_result.overall_score is not None:
            try:
                overall_score = int(round(matching_result.overall_score))
            except (TypeError, ValueError):
                pass

        analysis = Analysis(
            user_id=user_id,
            resume_id=resume.id,
            job_id=job.id,
            matching_result_json=_matching_result_to_json(
                matching_result, matching_refined
            ),
            overall_score=overall_score,
        )
        db.add(analysis)
        db.flush()

        rewritten_id: Optional[int] = None
        if rewrite_result is not None:
            rw = RewrittenResume(
                analysis_id=analysis.id,
                revised_resume_text=rewrite_result.revised_resume_text,
                changes_json={
                    "changes": [c.model_dump() for c in rewrite_result.changes]
                },
                llm_model=llm_model,
            )
            db.add(rw)
            db.flush()
            rewritten_id = rw.id

        db.commit()
        return (job.id, resume.id, analysis.id, rewritten_id)
    finally:
        db.close()
