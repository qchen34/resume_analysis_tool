"""
投递记录（Application）的仓储层：CRUD 与「从分析创建」。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.db.base import SessionLocal
from src.db.models import Application, Analysis, Job


def create(
    company: Optional[str] = None,
    role_title: Optional[str] = None,
    jd_summary_or_link: Optional[str] = None,
    location: Optional[str] = None,
    salary_range: Optional[str] = None,
    platform: Optional[str] = None,
    initiated_contact: Optional[bool] = None,
    resume_sent: Optional[bool] = None,
    has_reply: Optional[bool] = None,
    has_interview: Optional[bool] = None,
    interview_rounds: Optional[int] = None,
    interview_feedback: Optional[str] = None,
    offer_details: Optional[str] = None,
    analysis_id: Optional[int] = None,
    **kwargs: Any,
) -> Application:
    """创建一条投递记录。"""
    db = SessionLocal()
    try:
        app = Application(
            analysis_id=analysis_id,
            company=company,
            role_title=role_title,
            jd_summary_or_link=jd_summary_or_link,
            location=location,
            salary_range=salary_range,
            platform=platform,
            initiated_contact=initiated_contact,
            resume_sent=resume_sent,
            has_reply=has_reply,
            has_interview=has_interview,
            interview_rounds=interview_rounds,
            interview_feedback=interview_feedback,
            offer_details=offer_details,
        )
        db.add(app)
        db.commit()
        db.refresh(app)
        return app
    finally:
        db.close()


def get_by_id(application_id: int) -> Optional[Application]:
    """按 id 查询一条投递记录。"""
    db = SessionLocal()
    try:
        return db.get(Application, application_id)
    finally:
        db.close()


def list_applications(
    limit: int = 100,
    offset: int = 0,
    company: Optional[str] = None,
    has_reply: Optional[bool] = None,
    has_interview: Optional[bool] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> List[Application]:
    """
    列表查询，支持按公司、回复/面试状态、时间范围筛选。
    """
    db = SessionLocal()
    try:
        q = db.query(Application).order_by(Application.created_at.desc())
        if company:
            q = q.filter(Application.company.ilike(f"%{company}%"))
        if has_reply is not None:
            q = q.filter(Application.has_reply == has_reply)
        if has_interview is not None:
            q = q.filter(Application.has_interview == has_interview)
        if from_date is not None:
            q = q.filter(Application.created_at >= from_date)
        if to_date is not None:
            q = q.filter(Application.created_at <= to_date)
        return q.offset(offset).limit(limit).all()
    finally:
        db.close()


def update(
    application_id: int,
    **kwargs: Any,
) -> Optional[Application]:
    """更新一条投递记录，只更新传入的非 None 字段。"""
    db = SessionLocal()
    try:
        app = db.get(Application, application_id)
        if app is None:
            return None
        for k, v in kwargs.items():
            if hasattr(Application, k) and v is not None:
                setattr(app, k, v)
        db.commit()
        db.refresh(app)
        return app
    finally:
        db.close()


def delete(application_id: int) -> bool:
    """删除一条投递记录。"""
    db = SessionLocal()
    try:
        app = db.get(Application, application_id)
        if app is None:
            return False
        db.delete(app)
        db.commit()
        return True
    finally:
        db.close()


def create_from_analysis(
    analysis_id: int,
    **overrides: Any,
) -> Optional[Application]:
    """
    从某次分析创建投递记录：从 Analysis 及关联的 Job 预填公司、职位、JD 摘要等，
    其余字段由 overrides 覆盖或补充。
    """
    db = SessionLocal()
    try:
        analysis = db.get(Analysis, analysis_id)
        if analysis is None:
            return None
        job: Optional[Job] = db.get(Job, analysis.job_id) if analysis.job_id else None
        # 预填
        company = overrides.get("company")
        role_title = overrides.get("role_title")
        jd_summary_or_link = overrides.get("jd_summary_or_link")
        location = overrides.get("location")
        if job is not None:
            if company is None:
                company = job.company
            if role_title is None:
                role_title = job.title
            if location is None:
                location = job.location
            if jd_summary_or_link is None and job.raw_jd_text:
                jd_summary_or_link = (job.raw_jd_text[:500] + "…") if len(job.raw_jd_text) > 500 else job.raw_jd_text
        app = Application(
            analysis_id=analysis_id,
            company=company,
            role_title=role_title,
            jd_summary_or_link=jd_summary_or_link,
            location=location,
            salary_range=overrides.get("salary_range"),
            platform=overrides.get("platform"),
            initiated_contact=overrides.get("initiated_contact"),
            resume_sent=overrides.get("resume_sent", True),
            has_reply=overrides.get("has_reply"),
            has_interview=overrides.get("has_interview"),
            interview_rounds=overrides.get("interview_rounds"),
            interview_feedback=overrides.get("interview_feedback"),
            offer_details=overrides.get("offer_details"),
        )
        db.add(app)
        db.commit()
        db.refresh(app)
        return app
    finally:
        db.close()


def to_dict(app: Application) -> Dict[str, Any]:
    """将 Application ORM 转为字典，便于 JSON/前端展示。"""
    return {
        "id": app.id,
        "analysis_id": app.analysis_id,
        "company": app.company,
        "role_title": app.role_title,
        "jd_summary_or_link": (app.jd_summary_or_link or "")[:200],
        "location": app.location,
        "salary_range": app.salary_range,
        "platform": app.platform,
        "initiated_contact": app.initiated_contact,
        "resume_sent": app.resume_sent,
        "has_reply": app.has_reply,
        "has_interview": app.has_interview,
        "interview_rounds": app.interview_rounds,
        "interview_feedback": app.interview_feedback,
        "offer_details": app.offer_details,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
    }


def get_stats(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    按时间范围统计投递数据：投递数、有回复数、面试数、Offer 数及派生指标。
    """
    db = SessionLocal()
    try:
        q = db.query(Application)
        if from_date is not None:
            q = q.filter(Application.created_at >= from_date)
        if to_date is not None:
            q = q.filter(Application.created_at <= to_date)
        apps = q.all()
        total = len(apps)
        replied = sum(1 for a in apps if a.has_reply)
        interviewed = sum(1 for a in apps if a.has_interview)
        with_offer = sum(1 for a in apps if (a.offer_details or "").strip())
        return {
            "total": total,
            "replied": replied,
            "interviewed": interviewed,
            "with_offer": with_offer,
            "reply_rate": (replied / total * 100) if total else 0,
            "interview_rate": (interviewed / total * 100) if total else 0,
            "offer_rate": (with_offer / total * 100) if total else 0,
        }
    finally:
        db.close()
