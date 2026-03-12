from __future__ import annotations

from typing import Any, Dict, List

from src.models.schemas import JobProfile, ResumeProfile


def _collect_resume_text(resume: ResumeProfile) -> List[str]:
    parts: List[str] = []
    if resume.summary:
        parts.extend(resume.summary)
    for edu in resume.education:
        parts.append(edu.school or "")
        parts.extend(edu.highlights or [])
    for exp in resume.experiences:
        parts.append(exp.company or "")
        parts.append(exp.title or "")
        parts.extend(exp.bullets or [])
    for proj in resume.projects:
        parts.append(proj.name or "")
        parts.extend(proj.bullets or [])
    skills = (
        (resume.skills.languages or [])
        + (resume.skills.frameworks or [])
        + (resume.skills.tools or [])
        + (resume.skills.others or [])
    )
    parts.extend(skills)
    # 统一转成字符串列表，便于后续 lower + 搜索
    return [p for p in (str(x) for x in parts) if p]


def compute_keyword_match(job: JobProfile, resume: ResumeProfile) -> Dict[str, Any]:
    """
    只做“是否出现”的关键词匹配，不做任何主观分析。

    当前仅对 JobProfile.must_have_skills / nice_to_have_skills 做技能匹配：
    - present: 0/1，表示在简历文本中是否出现（大小写不敏感，简单子串匹配）。
    - count: 出现次数。
    """
    resume_texts = _collect_resume_text(resume)
    resume_blob = "\n".join(resume_texts)
    resume_blob_lower = resume_blob.lower()

    skill_matches: List[Dict[str, Any]] = []

    all_skills: List[str] = []
    all_skills.extend(job.must_have_skills or [])
    all_skills.extend(job.nice_to_have_skills or [])

    seen: set[str] = set()
    for skill in all_skills:
        s = (skill or "").strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)

        s_lower = s.lower()
        if not s_lower:
            continue

        count = resume_blob_lower.count(s_lower)
        present = 1 if count > 0 else 0

        skill_matches.append(
            {
                "jd_skill": s,
                "present": present,
                "count": count,
            }
        )

    return {
        "skill_matches": skill_matches,
    }

