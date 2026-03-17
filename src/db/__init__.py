"""
DB 包：任一入口（命令行 main.py 或 Streamlit app）首次导入 src.db 时确保表存在，
便于分析入库与投递 Tracker 读取；无需在 app.py 内单独初始化。
"""
from __future__ import annotations

try:
    from src.db.init_db import init_database
    init_database(drop_existing=False)
except Exception:
    pass  # 无写权限等时跳过，用户可手动执行 python -m src.db.init_db
