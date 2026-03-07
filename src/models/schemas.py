from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Education(BaseModel):
    school: str
    degree: Optional[str] = None
    major: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    gpa: Optional[str] = None
    highlights: List[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    title: str
    type: Optional[str] = Field(default=None, description="intern/full-time/part-time etc.")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    bullets: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    role: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    bullets: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    impact_metric: Optional[str] = None


class Skills(BaseModel):
    languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    others: List[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    name: Optional[str] = None
    headline: Optional[str] = Field(
        default=None,
        description="当前的职位或一句话标签，例如“AI 产品经理 / 5 年经验”。",
    )
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    summary: List[str] = Field(
        default_factory=list,
        description="简历顶部的个人总结要点列表。",
    )
    links: List[str] = Field(
        default_factory=list,
        description="个人主页、GitHub、作品集等链接。",
    )
    years_of_experience: Optional[float] = Field(
        default=None,
        description="总工作年限（单位：年），便于规则层快速过滤与打分。",
    )
    certifications: List[str] = Field(
        default_factory=list,
        description="与专业能力相关的证书或认证。",
    )
    spoken_languages: List[str] = Field(
        default_factory=list,
        description="自然语言能力（如中英双语、日语等），区别于编程语言。",
    )
    education: List[Education] = Field(default_factory=list)
    experiences: List[Experience] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)


class JobProfile(BaseModel):
    role_title: Optional[str] = None
    company: Optional[str] = None
    level: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    must_have_skills: List[str] = Field(default_factory=list)
    nice_to_have_skills: List[str] = Field(default_factory=list)
    responsibilities: List[str] = Field(default_factory=list)
    domain_keywords: List[str] = Field(default_factory=list)
    soft_skills: List[str] = Field(default_factory=list)
    values_keywords: List[str] = Field(default_factory=list)
    experience_requirements: Optional[str] = None


class MatchingDimensions(BaseModel):
    skills: float
    experience: float
    domain: float
    education: float
    soft_skills_signal: float
    leadership: float
    communication: float
    culture_fit: float


class GapItem(BaseModel):
    type: str
    name: str
    severity: str
    detail: Optional[str] = None


class MatchingResult(BaseModel):
    overall_score_raw: float
    overall_score: Optional[float] = Field(default=None, description="0-100 after LLM refinement")
    dimensions: MatchingDimensions
    gaps: List[GapItem] = Field(default_factory=list)
    explanation: Optional[str] = None
    dimension_explanations: Optional[Dict[str, str]] = Field(
        default=None,
        description="对各维度得分的简短文字解释，键为维度名。",
    )


class ResumeChange(BaseModel):
    section: str
    item_index: Optional[int] = None
    change_type: str
    old_text: Optional[str] = None
    new_text: Optional[str] = None


class RewriteResult(BaseModel):
    revised_resume_text: str
    changes: List[ResumeChange] = Field(default_factory=list)

