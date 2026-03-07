from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime
import os
import hashlib
import json
import traceback

from dotenv import load_dotenv

# 从脚本所在目录加载 .env，避免因运行目录不同而读不到配置
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path)

from src.analysis.matching_engine import compute_matching, compute_matching_with_details
from src.analysis.resume_rewriter import rewrite_resume_for_job
from src.llm.client import llm_client
from src.parsers.jd_parser import parse_jd
from src.parsers.resume_parser import parse_resume
from src.models.schemas import JobProfile, ResumeProfile, MatchingResult


def _usage_snapshot() -> Dict[str, Optional[int]]:
    usage = getattr(llm_client, "last_usage", None)
    if not usage:
        return {"input_tokens": None, "output_tokens": None, "total": None}
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", input_tokens)
        output_tokens = usage.get("output_tokens", output_tokens)
    total = (input_tokens or 0) + (output_tokens or 0) if (input_tokens is not None or output_tokens is not None) else None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total": total,
    }


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    # 再次从项目根加载 .env，避免被其他模块的 load_dotenv()（从 cwd 加载）覆盖
    load_dotenv(base_dir / ".env", override=True)

    jd_path = base_dir / "example_data" / "jd_example_1.md"
    resume_path = base_dir / "example_data" / "resume_example_1.md"

    print("[1/7] 读取 JD 与简历...")
    jd_text = jd_path.read_text(encoding="utf-8")
    resume_text = resume_path.read_text(encoding="utf-8")
    print(f"      JD: {jd_path.name}, 简历: {resume_path.name}")

    # 基于文本内容的缓存键，避免重复请求 LLM
    cache_root = base_dir / "test_outputs" / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    key_str = jd_text + "\n====RESUME====\n" + resume_text
    cache_key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    cache_path = cache_root / f"{cache_key}.json"

    force_reanalyze = os.getenv("FORCE_REANALYZE", "false").lower() == "true"

    if not force_reanalyze and cache_path.exists():
        print("[2/7] 使用缓存跳过 JD/简历/匹配解析（若需重新解析请设 FORCE_REANALYZE=true）")
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        job_profile = JobProfile.model_validate(data["job_profile"])
        job_json = data["job_json"]
        jd_usage = data["jd_usage"]

        resume_profile = ResumeProfile.model_validate(data["resume_profile"])
        resume_json = data["resume_json"]
        resume_usage = data["resume_usage"]

        matching_result = MatchingResult.model_validate(data["matching_result"])
        matching_refined = data["matching_refined"]
        match_usage = data["match_usage"]
    else:
        print("[2/7] 解析 JD...")
        job_profile, job_json = parse_jd(jd_text)
        jd_usage = _usage_snapshot()
        print("      完成")

        print("[3/7] 解析简历...")
        resume_profile, resume_json = parse_resume(resume_text)
        resume_usage = _usage_snapshot()
        print("      完成")

        print("[4/7] 匹配分析（规则 + LLM 精炼 + 职责/任职覆盖）...")
        matching_result, matching_refined = compute_matching_with_details(resume_profile, job_profile)
        match_usage = _usage_snapshot()
        print("      完成")

        cache_data: Dict[str, Any] = {
            "job_profile": job_profile.model_dump(mode="json"),
            "job_json": job_json,
            "jd_usage": jd_usage,
            "resume_profile": resume_profile.model_dump(mode="json"),
            "resume_json": resume_json,
            "resume_usage": resume_usage,
            "matching_result": matching_result.model_dump(mode="json"),
            "matching_refined": matching_refined,
            "match_usage": match_usage,
        }
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[5/7] 生成 Markdown 报告...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = base_dir / "test_outputs"
    output_dir = output_root / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    # 4.1 JD 分析报告
    jd_lines: list[str] = []
    jd_lines.append("# JD 分析报告\n")
    jd_lines.append(f"- **JD 文件**: `{jd_path}`\n")
    jd_lines.append("## JDParser 结构化输出（简要）\n")
    jd_lines.append(f"`{repr(job_profile)}`\n")
    jd_lines.append("## JDParser 原始 JSON\n")
    jd_lines.append("```json")
    jd_lines.append(job_json)
    jd_lines.append("```")
    jd_lines.append(
        f"- **Token 使用（JDParser）**: input={jd_usage['input_tokens']}, "
        f"output={jd_usage['output_tokens']}, total={jd_usage['total']}\n"
    )
    jd_report_path = output_dir / "jd_analysis.md"
    jd_report_path.write_text("\n".join(jd_lines), encoding="utf-8")

    # 4.2 简历分析报告
    resume_lines: list[str] = []
    resume_lines.append("# 简历分析报告\n")
    resume_lines.append(f"- **简历文件**: `{resume_path}`\n")
    resume_lines.append("## ResumeParser 结构化输出（简要）\n")
    resume_lines.append(f"`{repr(resume_profile)}`\n")
    resume_lines.append("## ResumeParser 原始 JSON\n")
    resume_lines.append("```json")
    resume_lines.append(resume_json)
    resume_lines.append("```")
    resume_lines.append(
        f"- **Token 使用（ResumeParser）**: input={resume_usage['input_tokens']}, "
        f"output={resume_usage['output_tokens']}, total={resume_usage['total']}\n"
    )
    resume_report_path = output_dir / "resume_analysis.md"
    resume_report_path.write_text("\n".join(resume_lines), encoding="utf-8")

    # 4.3 匹配结果报告
    match_lines: list[str] = []
    match_lines.append("# Matching 报告\n")
    match_lines.append(f"- **JD 文件**: `{jd_path}`")
    match_lines.append(f"- **简历文件**: `{resume_path}`\n")

    match_lines.append("## MatchingResult（结构化）\n")
    match_lines.append("```json")
    match_lines.append(matching_result.model_dump_json(indent=2, ensure_ascii=False))
    match_lines.append("```")

    # 职责覆盖度
    coverage = matching_refined.get("responsibility_coverage")
    match_lines.append("## JD 职责覆盖度\n")
    if coverage:
        match_lines.append("```json")
        match_lines.append(json.dumps(coverage, ensure_ascii=False, indent=2))
        match_lines.append("```")
    else:
        match_lines.append("_LLM 未返回 responsibility_coverage 字段或为空。_")

    # 任职要求覆盖度
    skill_cov = matching_refined.get("skill_coverage")
    match_lines.append("## 任职要求覆盖度\n")
    if skill_cov:
        match_lines.append("```json")
        match_lines.append(json.dumps(skill_cov, ensure_ascii=False, indent=2))
        match_lines.append("```")
    else:
        match_lines.append("_LLM 未返回 skill_coverage 字段或为空。_")

    match_lines.append(
        f"- **Token 使用（MatchingEngine）**: input={match_usage['input_tokens']}, "
        f"output={match_usage['output_tokens']}, total={match_usage['total']}\n"
    )

    match_report_path = output_dir / "matching_report.md"
    match_report_path.write_text("\n".join(match_lines), encoding="utf-8")
    print(f"      报告目录: {output_dir}")

    print("[6/7] 简历重写（含可选通顺性审核）...")
    rewrite_result = rewrite_resume_for_job(resume_text, job_profile, matching_result)
    rewrite_usage = _usage_snapshot()

    # 5.1 修改后的完整简历
    rewritten_lines: list[str] = []
    rewritten_lines.append("# 针对当前 JD 优化后的简历\n")
    rewritten_lines.append(f"- **对应 JD**: `{jd_path}`\n")
    rewritten_lines.append("---\n\n")
    rewritten_lines.append(rewrite_result.revised_resume_text)
    rewritten_path = output_dir / "rewritten_resume.md"
    rewritten_path.write_text("\n".join(rewritten_lines), encoding="utf-8")

    # 5.2 重写变更报告（修改清单）
    change_lines: list[str] = []
    change_lines.append("# 简历重写变更报告\n")
    change_lines.append(f"- **对应 JD**: `{jd_path}`")
    change_lines.append(f"- **简历文件**: `{resume_path}`\n")
    change_lines.append("## 变更清单\n")
    for i, c in enumerate(rewrite_result.changes, 1):
        idx_str = f" 条目 {c.item_index}" if c.item_index is not None else ""
        change_lines.append(f"### {i}. [{c.change_type}] {c.section}{idx_str}\n")
        if c.old_text:
            change_lines.append(f"- **原文**: {c.old_text}\n")
        if c.new_text:
            change_lines.append(f"- **修改后**: {c.new_text}\n")
    change_lines.append(
        f"- **Token 使用（ResumeRewriter）**: input={rewrite_usage['input_tokens']}, "
        f"output={rewrite_usage['output_tokens']}, total={rewrite_usage['total']}\n"
    )
    rewrite_report_path = output_dir / "rewrite_report.md"
    rewrite_report_path.write_text("\n".join(change_lines), encoding="utf-8")
    print("      完成")

    print("[7/7] 收尾与入库...")
    _raw = os.getenv("SAVE_TO_DB", "")
    save_to_db = (_raw or "").strip().lower() == "true"
    if not save_to_db:
        print(f"      [提示] 当前 SAVE_TO_DB={_raw!r}（.env 路径: {base_dir / '.env'}，存在: {(base_dir / '.env').exists()}）")
    elif save_to_db:
        print("      SAVE_TO_DB=true，正在写入数据库...")
    print("测试报告已生成:")
    print(f"- JD 报告: {jd_report_path}")
    print(f"- 简历报告: {resume_report_path}")
    print(f"- Matching 报告: {match_report_path}")
    print(f"- 重写后简历: {rewritten_path}")
    print(f"- 重写变更报告: {rewrite_report_path}")

    if save_to_db:
        try:
            from src.db.analysis_run_repo import save_analysis_run
            job_id, resume_id, analysis_id, rewritten_id = save_analysis_run(
                jd_text=jd_text,
                resume_text=resume_text,
                job_profile=job_profile,
                resume_profile=resume_profile,
                matching_result=matching_result,
                matching_refined=matching_refined,
                rewrite_result=rewrite_result,
                user_id=None,
                llm_model=os.getenv("GEMINI_MODEL"),
            )
            print(f"      已入库: job_id={job_id}, resume_id={resume_id}, analysis_id={analysis_id}, rewritten_resume_id={rewritten_id}")
        except Exception as e:
            print(f"      入库失败（报告已生成）: {e}")
            traceback.print_exc()
    else:
        print("      未启用入库（SAVE_TO_DB≠true）。若需写入数据库请在 .env 中设置 SAVE_TO_DB=true")


if __name__ == "__main__":
    main()
