"""
将简历重写结果写入 rewritten_resumes 表。由 CLI 或分析流程在获得 RewriteResult 后调用。
"""
from __future__ import annotations

from typing import Optional

from src.db.base import SessionLocal
from src.db.models import RewrittenResume
from src.models.schemas import RewriteResult


def save_rewritten_resume(
    analysis_id: int,
    result: RewriteResult,
    llm_model: Optional[str] = None,
) -> int:
    """
    把一次重写结果写入 rewritten_resumes 表。
    返回新插入记录的 id。
    """
    changes_data = [c.model_dump() for c in result.changes]
    row = RewrittenResume(
        analysis_id=analysis_id,
        revised_resume_text=result.revised_resume_text,
        changes_json={"changes": changes_data},
        llm_model=llm_model,
    )
    db = SessionLocal()
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()
