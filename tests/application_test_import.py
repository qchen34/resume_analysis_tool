from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 将项目根目录加入 sys.path，方便直接 `python3 tests/application_test_import.py` 运行
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _flag_01(v: Any) -> Optional[int]:
    """
    将常见的「是/否、True/False、1/0、已回复/未回复」等值映射为 0/1/None。
    - 返回 1：明确为“是/有/已”
    - 返回 0：明确为“否/无/未”
    - 返回 None：空值或无法判断
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None

    s_lower = s.lower()
    if s_lower in {"1", "true", "yes", "y"}:
        return 1
    if s_lower in {"0", "false", "no", "n"}:
        return 0

    if s in {"是", "有", "已"}:
        return 1
    if s in {"否", "无", "未"}:
        return 0

    # 兼容 boss 等状态文案
    if "已回复" in s or "有回复" in s or (s.startswith("已") and "回" in s):
        return 1
    if "未回复" in s or "未回" in s:
        return 0
    if "已读未回" in s or "已读" in s or "已送达" in s:
        return 0

    # “筛选中/处理中”等不当作 0，保留 None
    if "筛选" in s or "处理中" in s or "进行中" in s:
        return None

    return None


def _parse_mmdd(mmdd: str, year: int) -> Optional[datetime]:
    """
    解析类似 '0306'、'0318' 这种 MMDD 为 datetime（UTC）。
    """
    s = (mmdd or "").strip()
    if len(s) != 4 or not s.isdigit():
        return None
    month = int(s[:2])
    day = int(s[2:])
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _map_row(row: Dict[str, str], default_year: int) -> Dict[str, Any]:
    """
    将 CSV 行映射到 application_repo.create(...) 的参数。
    适配当前 DB：
    - initiated_contact/resume_sent/has_reply/has_interview/offer: 0/1/None
    - offer_details: 文本备注
    """
    company = (row.get("公司名称") or "").strip() or None
    role_title = (row.get("职位") or "").strip() or None
    jd_text = (row.get("JD") or "").strip() or None
    salary_range = (row.get("薪资范围") or "").strip() or None
    location = (row.get("工作地点") or "").strip() or None
    platform = (row.get("沟通渠道") or "").strip() or None

    initiated_contact = _flag_01(row.get("是否HR主动"))
    # 注意：原表里“是否发送简历”为“是/否”
    resume_sent = _flag_01(row.get("是否发送简历"))

    # “打招呼是否回复”是更细的状态：已回复/已读未回/已送达/已送达 等
    has_reply = _flag_01(row.get("打招呼是否回复"))

    # “是否面试邀约”可能出现 “是/否/简历筛选中”
    has_interview = _flag_01(row.get("是否面试邀约"))

    # Offer：CSV 没有明确字段，先默认 0；如你后续补充，可在 DB/表格中编辑
    offer = 0

    # 备注：优先用 Comment，其次拼接 HR 反馈/候选人回复（避免丢信息）
    comment = (row.get("Comment") or "").strip()
    hr_fb = (row.get("HR第一次反馈") or "").strip()
    cand_fb = (row.get("候选人第一次回复") or "").strip()
    offer_details_parts = [p for p in [comment, hr_fb, cand_fb] if p]
    offer_details = "\n".join(offer_details_parts) if offer_details_parts else None

    created_at = _parse_mmdd(row.get("初次沟通日期", ""), default_year)

    return {
        "company": company,
        "role_title": role_title,
        "jd_summary_or_link": jd_text,
        "location": location,
        "salary_range": salary_range,
        "platform": platform,
        "initiated_contact": initiated_contact,
        "resume_sent": resume_sent,
        "has_reply": has_reply,
        "has_interview": has_interview,
        "offer": offer,
        "offer_details": offer_details,
        "created_at": created_at,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将求职投递 CSV 导入 applications 表（适配当前 DB 字段）。"
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        default="tests/第一周复盘.csv",
        help="CSV 文件路径（默认使用你本机的绝对路径）。",
    )
    parser.add_argument(
        "--year",
        dest="year",
        type=int,
        default=datetime.utcnow().year,
        help="解析 MMDD 日期时使用的年份（默认当前年份）。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析不入库（用于检查映射结果）。",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.is_file():
        raise SystemExit(f"CSV 不存在：{csv_path}")

    # 延迟导入，避免脚本被当成库引用时触发 DB 连接
    from src.db.application_repo import create as create_application
    from src.db.base import SessionLocal
    from src.db.models import Application

    rows: list[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # 空行（公司名、职位等都为空）直接跳过
            if not any((v or "").strip() for v in r.values()):
                continue
            rows.append(r)

    print(f"读取到 {len(rows)} 行（已跳过纯空行）。")

    mapped = [_map_row(r, default_year=args.year) for r in rows]
    # 预览前 3 行映射结果
    for i, m in enumerate(mapped[:3], start=1):
        preview = {k: m[k] for k in ["company", "role_title", "platform", "resume_sent", "has_reply", "has_interview", "offer"]}
        print(f"[预览 {i}] {preview}")

    if args.dry_run:
        print("dry-run：未写入数据库。")
        return

    db = SessionLocal()
    try:
        created = 0
        for m in mapped:
            created_at = m.pop("created_at", None)
            app = create_application(**m)
            # 回填 created_at（create() 里默认会用 datetime.utcnow）
            if created_at is not None:
                rec = db.get(Application, app.id)
                if rec is not None:
                    rec.created_at = created_at
            created += 1
            if created % 20 == 0:
                db.commit()
                print(f"已导入 {created} 条…")
        db.commit()
        print(f"导入完成，共写入 {created} 条 applications 记录。")
    finally:
        db.close()


if __name__ == "__main__":
    main()

