import sys
from pathlib import Path
# 将项目根目录加入 sys.path，方便直接 `python tests/seed_applications_demo.py` 运行
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from datetime import datetime, timedelta
from src.db.application_repo import create
from src.db.base import SessionLocal
from src.db.models import Application


def main() -> None:
    print("创建演示用 applications 投递记录...")

    now = datetime.utcnow()

    samples = [
        dict(
            company="科大讯飞",
            role_title="海外售前解决方案经理",
            jd_summary_or_link="负责海外大客户售前方案设计与技术支持，覆盖语音识别、NLP 等 AI 产品线。",
            location="深圳",
            salary_range="30-40K×14",
            platform="Boss直聘",
            resume_sent=1,
            has_reply=1,
            has_interview=1,
            interview_rounds=2,
            offer=0,
            offer_details="",
        ),
        dict(
            company="字节跳动",
            role_title="增长产品经理",
            jd_summary_or_link="负责国际化增长产品线，搭建 A/B 实验平台与用户增长策略。",
            location="北京",
            salary_range="40-55K×16",
            platform="内推",
            resume_sent=1,
            has_reply=1,
            has_interview=0,
            interview_rounds=None,
            offer=0,
            offer_details="",
        ),
        dict(
            company="美团",
            role_title="高级数据产品经理",
            jd_summary_or_link="负责数据平台 & 指标治理，服务内部运营与业务分析团队。",
            location="上海",
            salary_range="35-45K×15",
            platform="拉勾",
            resume_sent=1,
            has_reply=0,
            has_interview=0,
            interview_rounds=None,
            offer=0,
            offer_details="",
        ),
        dict(
            company="某 AIGC 独角兽",
            role_title="AI 产品负责人",
            jd_summary_or_link="从 0-1 规划新一代 AIGC 产品，聚焦创作工具与工作流自动化。",
            location="远程 / 北京",
            salary_range="面议（股权+现金）",
            platform="猎头",
            resume_sent=1,
            has_reply=1,
            has_interview=1,
            interview_rounds=3,
            offer=1,
            offer_details="综合 package 优于 BAT 标准，但需要 relocation。",
        ),
        dict(
            company="某外企云厂商",
            role_title="解决方案架构师",
            jd_summary_or_link="云原生与数据智能方向，为大客户提供端到端解决方案。",
            location="上海",
            salary_range="45-60K×16",
            platform="LinkedIn",
            resume_sent=0,
            has_reply=None,
            has_interview=None,
            interview_rounds=None,
            offer=0,
            offer_details="",
        ),
        # 再加几条，让趋势图和公司分布更丰富
        dict(
            company="阿里巴巴",
            role_title="高级算法工程师",
            jd_summary_or_link="搜索与推荐算法，负责召回与排序策略优化。",
            location="杭州",
            salary_range="45-65K×16",
            platform="内推",
            resume_sent=1,
            has_reply=1,
            has_interview=1,
            interview_rounds=3,
            offer=0,
            offer_details="",
        ),
        dict(
            company="腾讯",
            role_title="数据科学家",
            jd_summary_or_link="负责风控与增长方向的数据建模与实验设计。",
            location="深圳",
            salary_range="40-55K×15",
            platform="校园招聘",
            resume_sent=1,
            has_reply=0,
            has_interview=0,
            interview_rounds=None,
            offer=0,
            offer_details="",
        ),
        dict(
            company="小红书",
            role_title="用户增长产品经理",
            jd_summary_or_link="负责社区增长、留存与召回策略的设计与落地。",
            location="上海",
            salary_range="35-50K×15",
            platform="Boss直聘",
            resume_sent=1,
            has_reply=1,
            has_interview=1,
            interview_rounds=1,
            offer=0,
            offer_details="首轮面试表现良好，等待二面安排。",
        ),
        dict(
            company="微软中国",
            role_title="云解决方案架构师",
            jd_summary_or_link="Azure 云平台，帮助客户完成上云与架构设计。",
            location="北京",
            salary_range="面议",
            platform="LinkedIn",
            resume_sent=1,
            has_reply=1,
            has_interview=0,
            interview_rounds=None,
            offer=0,
            offer_details="",
        ),
        dict(
            company="创业公司 X",
            role_title="全栈工程师",
            jd_summary_or_link="负责从前端到后端的一条龙开发，技术栈 React + Python。",
            location="远程",
            salary_range="20-30K×14",
            platform="朋友内推",
            resume_sent=1,
            has_reply=1,
            has_interview=1,
            interview_rounds=2,
            offer=1,
            offer_details="整体 package 略低，但工作内容契合度高。",
        ),
    ]

    db = SessionLocal()
    try:
        for i, payload in enumerate(samples, start=1):
            app = create(**payload)
            # 为了可视化更好看，将 created_at 均匀分布在最近若干天
            rec = db.get(Application, app.id)
            if rec:
                rec.created_at = now - timedelta(days=i * 3)
            print(f"  - id={app.id}, company={app.company}, role={app.role_title}")
        db.commit()
    finally:
        db.close()

    print("演示数据创建完成。请在 Streamlit 的『投递记录 (Tracker)』Tab 中查看。")


if __name__ == "__main__":
    main()