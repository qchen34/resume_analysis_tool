from __future__ import annotations

from typing import Any, Dict, List, Tuple
import os

from tavily import TavilyClient

from src.models.schemas import JobProfile

# 结果过滤：摘要中至少包含其一则保留，减少与招聘/求职无关的噪音（可通过 TAVILY_FILTER_RESULTS=false 关闭）
_RELEVANCE_KEYWORDS = ("招聘", "求职", "面试", "岗位", "公司", "JD", "简历", "职位", "面经", "offer", "入职")


def _build_search_plan(job: JobProfile) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """
    根据 JD 结构化信息构造 Tavily 搜索计划（只返回“查什么”和“为什么这样查”的元数据，不触网）。
    """
    company = (job.company or "").strip()
    role = (job.role_title or "").strip()
    location = (job.location or "").strip()
    domain_keywords = job.domain_keywords or []

    queries: List[Dict[str, str]] = []

    # 1. 公司层面：介绍、规模、人数、融资/市值（便于后续总结公司画像）
    if company:
        queries.append(
            {
                "type": "company_overview",
                "query": f"{company} 公司 介绍 背景 业务 规模 人数 融资 市值 招聘",
                "keywords": company,
            }
        )

    # 2. 岗位层面：岗位职责 / 任职要求（加「招聘」「岗位要求」）
    if company and role:
        queries.append(
            {
                "type": "role_detail",
                "query": f"{company} {role} 招聘 岗位 职责 任职要求 工作内容",
                "keywords": f"{company} | {role}",
            }
        )
    elif role:
        queries.append(
            {
                "type": "role_detail",
                "query": f"{role} 招聘 岗位 职责 任职要求 工作内容",
                "keywords": role,
            }
        )

    # 3. 面试相关：面试经验 / 难度 / 高频问题（加「面试」强化）
    if company and role:
        queries.append(
            {
                "type": "interview_experience",
                "query": f"{company} {role} 面试 经验 难度 面经 高频问题",
                "keywords": f"{company} | {role}",
            }
        )
    elif company:
        queries.append(
            {
                "type": "interview_experience",
                "query": f"{company} 面试 经验 面经 难度",
                "keywords": company,
            }
        )

    # 4. 行业 / 技术大环境：趋势、主要玩家、竞争格局（便于判断行业梯队）
    if domain_keywords:
        top_keywords = " ".join(domain_keywords[:5])
        queries.append(
            {
                "type": "industry_trend",
                "query": f"{top_keywords} 行业 技术 趋势 主要玩家 排名 竞争格局 人才需求",
                "keywords": top_keywords,
            }
        )

    meta = {
        "company": company or None,
        "role_title": role or None,
        "location": location or None,
        "domain_keywords": domain_keywords,
    }
    return queries, meta


def run_tavily_search(job: JobProfile) -> Dict[str, Any]:
    """
    针对“公司 / 岗位 / 行业大环境”做 Tavily 搜索。

    返回的字典结构示例：
    {
      "enabled": true,
      "company": "SenseTime 商汤科技",
      "role_title": "大装置-售前解决方案经理",
      "location": "深圳",
      "domain_keywords": [...],
      "queries": [
        {"type": "company_overview", "query": "...", "keywords": "..."},
        ...
      ],
      "search_results": [
        {
          "type": "company_overview",
          "query": "...",
          "summary": "...",          # Tavily 的 answer 字段
          "links": [                 # 精简后的链接列表
            {"title": "...", "url": "..."},
            ...
          ],
        },
        ...
      ],
    }
    """
    api_key = os.getenv("TAVILY_API_KEY")
    queries, meta = _build_search_plan(job)

    # 如果未配置 API Key，则只返回“搜索计划”，不实际请求 Tavily
    if not api_key:
        return {
            "enabled": False,
            "reason": "TAVILY_API_KEY 未配置，仅返回搜索计划（未实际调用 Tavily）。",
            **meta,
            "queries": queries,
            "search_results": [],
        }

    client = TavilyClient(api_key=api_key)
    search_results: List[Dict[str, Any]] = []

    for item in queries:
        q_type = item["type"]
        q = item["query"]
        try:
            resp: Dict[str, Any] = client.search(q, search_depth="advanced", max_results=5)  # type: ignore[assignment]
            summary = resp.get("answer") or ""
            links: List[Dict[str, str]] = []
            for r in (resp.get("results") or [])[:3]:
                title = r.get("title") or ""
                url = r.get("url") or ""
                if not url:
                    continue
                links.append({"title": title, "url": url})
            # 可选：仅保留与招聘/求职相关的摘要（TAVILY_FILTER_RESULTS 默认 true）
            filter_enabled = os.getenv("TAVILY_FILTER_RESULTS", "true").strip().lower() == "true"
            if filter_enabled and summary and not any(kw in summary for kw in _RELEVANCE_KEYWORDS):
                summary = "(摘要未包含招聘/求职相关关键词，已过滤；可设 TAVILY_FILTER_RESULTS=false 关闭)"
            search_results.append(
                {
                    "type": q_type,
                    "query": q,
                    "summary": summary,
                    "links": links,
                }
            )
        except Exception as exc:  # noqa: BLE001
            search_results.append(
                {
                    "type": q_type,
                    "query": q,
                    "error": str(exc),
                    "summary": "",
                    "links": [],
                }
            )

    return {
        "enabled": True,
        **meta,
        "queries": queries,
        "search_results": search_results,
    }

