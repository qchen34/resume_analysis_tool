from __future__ import annotations

import os

from sqlalchemy import inspect

from src.db.base import Base, engine, DATABASE_URL
from src.db import models  # noqa: F401  # 导入以注册所有 ORM 模型


def ensure_data_dir() -> None:
    """
    确保 SQLite 使用的 data 目录存在（仅当使用默认 sqlite:///./data/... 时需要）。
    其他类型数据库（如 Postgres）不需要此步骤。
    """
    if DATABASE_URL.startswith("sqlite:///./"):
        # sqlite:///./data/resume_analysis.db -> ./data
        path = DATABASE_URL.replace("sqlite:///", "")
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)


def init_database(drop_existing: bool = False) -> None:
    """
    初始化数据库：
    - 可选地先删除已有表结构（drop_existing=True）
    - 再创建所有表。
    """
    ensure_data_dir()

    inspector = inspect(engine)
    has_tables = inspector.get_table_names()

    if drop_existing and has_tables:
        Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_database()
    print("Database initialized based on models in src.db.models.")

