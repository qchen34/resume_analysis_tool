from __future__ import annotations

"""
tracker_review.py

针对投递 Tracker（applications 表）做一次「多轮次大牛分析」：
- 从数据库中拉取最近一段时间的投递记录（默认 30 天，可通过 TRACKER_REVIEW_DAYS 配置）。
- 根据 .env 中的 DEBATE_PERSONAS 选择大牛角色，对整体投递表现进行逐人点评。
- 输出：
  - test_outputs/tracker_<timestamp>/tracker_review_<persona_id>.md ：每位大牛的分析报告；
  - test_outputs/tracker_<timestamp>/tracker_review_summary.md ：总结性报告；
  - memory/memory.md ：累计的精简记忆，每次分析都会读取并纳入上下文，再追加一条新记忆。
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from src.db.base import SessionLocal
from src.db.models import Application
from src.llm.client import llm_client
from src.llm.debate_personas import (
    get_enabled_personas_from_env,
    get_persona,
    draw_stance,
)
from src.llm.prompts import STRATEGY_SUMMARY_SYSTEM_PROMPT


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


def _load_applications(days: int) -> List[Application]:
    """拉取最近 N 天的投递记录。"""
    db = SessionLocal()
    try:
        to_date = datetime.utcnow()
        from_date = to_date - timedelta(days=days)
        q = (
            db.query(Application)
            .filter(Application.created_at >= from_date)
            .order_by(Application.created_at.desc())
        )
        return q.all()
    finally:
        db.close()


def _applications_to_text(apps: List[Application]) -> str:
    """将 applications 列表转为 LLM 友好的文本摘要。"""
    if not apps:
        return "最近没有任何投递记录。"
    lines: List[str] = []
    for a in apps:
        status = []
        if a.resume_sent:
            status.append("已投简历")
        if a.has_reply:
            status.append("有回复")
        if a.has_interview:
            status.append("有面试")
        if (a.offer_details or "").strip():
            status.append("有 Offer 线索")
        status_str = "；".join(status) if status else "状态待更新"
        created = a.created_at.strftime("%Y-%m-%d") if a.created_at else "未知日期"
        lines.append(
            f"- [{created}] 公司：{a.company or '（未填）'}；岗位：{a.role_title or '（未填）'}；"
            f"城市：{a.location or '（未填）'}；薪资：{a.salary_range or '（未填）'}；"
            f"平台：{a.platform or '（未填）'}；状态：{status_str}"
        )
    return "\n".join(lines)


def _read_memory(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _append_memory(path: Path, summary: str, timestamp: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = _read_memory(path)
    lines: List[str] = []
    if prev.strip():
        lines.append(prev.strip())
        lines.append("")
    lines.append(f"## {timestamp} Tracker 分析记忆")
    # 精简一下：只保留前几段/行
    snippet = "\n".join(summary.strip().splitlines()[:8])
    lines.append(snippet)
    lines.append("")  # 末尾空行
    path.write_text("\n".join(lines), encoding="utf-8")


def _analyze_with_persona(persona_id: str, tracker_text: str, memory_text: str) -> str:
    """对 tracker 进行单个大牛视角的点评，返回 Markdown 文本。"""
    conf = get_persona(persona_id)
    if not conf:
        return f"# {persona_id}\n\n未找到该 persona 配置。"

    stance = draw_stance()
    display_name = conf.get("display_name", persona_id)
    description = conf.get("description", "")

    system_prompt = (
        f"你是 {display_name}，请基于你的世界观与风格，对候选人最近一段时间的求职投递进行深度复盘与点评。\n"
        f"本次你的立场是：**{stance}**（看好 或 看空），请在分析中体现这一立场，但仍保持理性、真诚和可执行性建议。\n\n"
        f"人物设定：\n{description}\n\n"
        "你会看到：\n"
        "1）最近一段时间的投递记录（公司、岗位、地点、薪资、平台、回复/面试/Offer 状态等）；\n"
        "2）过往 Tracker 分析记忆（memory），代表你之前给过的建议和对候选人的理解。\n\n"
        "请输出一份 Markdown 格式的分析报告，结构建议：\n"
        "- 总体判断：一句话总结你对当前求职策略的看法；\n"
        "- 亮点：2-4 条你认为做得不错的地方；\n"
        "- 风险与问题：2-4 条需要警惕或改进的点；\n"
        "- 具体建议：从岗位选择、公司选择、城市/薪资期望、节奏安排等角度，给出可执行的下一步行动建议。\n"
        "风格可以保持你一贯的口吻，但不要骂人，不要泄露隐私。\n"
    )

    user_parts = [
        "【最近投递记录】",
        tracker_text,
    ]
    if memory_text.strip():
        user_parts.append("\n【过往记忆（节选，可参考但不必完全重复）】\n")
        user_parts.append(memory_text.strip()[:2000])

    user_prompt = "\n".join(user_parts)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return llm_client.chat(messages)


def _summarize_tracker(tracker_text: str, memory_text: str, persona_summaries: Dict[str, str]) -> str:
    """
    生成一个总结性报告，使用已有的 STRATEGY_SUMMARY_SYSTEM_PROMPT，
    将 tracker 文本 + 过往 memory + 本轮各大牛要点作为输入。
    """
    stats_text = tracker_text
    # 拼出一个简短的「本轮大牛观点摘要」段落
    persona_snippets: List[str] = []
    for pid, content in persona_summaries.items():
        snippet = "\n".join(content.strip().splitlines()[:5])
        persona_snippets.append(f"### {pid}\n{snippet}\n")
    persona_block = "\n".join(persona_snippets) if persona_snippets else "无单独人物点评。"

    system_prompt = STRATEGY_SUMMARY_SYSTEM_PROMPT
    user_prompt = (
        "下面是候选人最近一段时间的投递记录文本、之前的记忆摘要，以及本轮多位大牛的简要观点节选。\n\n"
        "【投递记录文本】\n"
        f"{stats_text}\n\n"
        "【过往记忆 memory（可参考，不必重复）】\n"
        f"{memory_text[:2000]}\n\n"
        "【本轮大牛观点节选】\n"
        f"{persona_block}\n\n"
        "请基于上述信息，用简明的中文给出当前求职策略的整体评估与下一步建议。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return llm_client.chat(messages)


def main() -> None:
    days = int(os.getenv("TRACKER_REVIEW_DAYS", "30") or "30")
    print(f"Tracker Review：最近 {days} 天的投递记录。")

    apps = _load_applications(days=days)
    if not apps:
        print("最近没有任何投递记录，退出。")
        return

    tracker_text = _applications_to_text(apps)

    # 记忆文件
    memory_path = BASE_DIR / "memory" / "memory.md"
    memory_text = _read_memory(memory_path)

    # 输出目录：test_outputs/tracker_<timestamp>/
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = BASE_DIR / "test_outputs" / f"tracker_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 选择大牛（与 .env 一致）
    personas = get_enabled_personas_from_env()
    if not personas:
        print("警告：DEBATE_PERSONAS 为空，本次仅生成汇总报告，不做多角色分析。")
        personas = []

    persona_summaries: Dict[str, str] = {}
    for pid in personas:
        print(f"→ 对 Tracker 进行大牛分析：{pid}")
        report = _analyze_with_persona(pid, tracker_text, memory_text)
        persona_summaries[pid] = report
        report_path = out_dir / f"tracker_review_{pid}.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"  已写入: {report_path}")

    # 总结性报告
    print("→ 生成总结性 Tracker 报告")
    summary = _summarize_tracker(tracker_text, memory_text, persona_summaries)
    summary_path = out_dir / "tracker_review_summary.md"
    summary_path.write_text(summary, encoding="utf-8")
    print(f"  已写入: {summary_path}")

    # 更新 memory
    _append_memory(memory_path, summary, timestamp)
    print(f"已更新 memory 文件: {memory_path}")


if __name__ == "__main__":
    main()

