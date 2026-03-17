from __future__ import annotations

from dotenv import load_dotenv
from pathlib import Path

# 从项目根加载 .env
load_dotenv(Path(__file__).resolve().parent / ".env")

from src.cli.run_once import run_once


def main() -> None:
    run_once()


if __name__ == "__main__":
    main()
