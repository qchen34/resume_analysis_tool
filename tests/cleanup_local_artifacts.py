from __future__ import annotations

"""
清理本地内测/运行产物（可重复执行）。

默认行为（都为 True）：
1) 清空 memory/memory.md 的内容（保留文件本身）
2) 删除 test_outputs/ 下所有报告/目录（保留 test_outputs/.gitkeep）

使用方式：
  python3 tests/cleanup_local_artifacts.py
"""

from pathlib import Path

# -----------------------------
# 你可以在这里快速修改开关（默认都为 True）
# -----------------------------
DELETE_MEMORY_CONTENT = True
DELETE_TEST_OUTPUTS = True


BASE_DIR = Path(__file__).resolve().parents[1]
MEMORY_FILE = BASE_DIR / "memory" / "memory.md"
TEST_OUTPUTS_DIR = BASE_DIR / "test_outputs"
KEEP_FILE = TEST_OUTPUTS_DIR / ".gitkeep"


def _truncate_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)
        return
    # directory
    for child in path.iterdir():
        _remove_tree(child)
    path.rmdir()


def main() -> None:
    print("开始清理本地产物…")

    if DELETE_MEMORY_CONTENT:
        if MEMORY_FILE.exists():
            _truncate_file(MEMORY_FILE)
            print(f"✅ 已清空 memory：{MEMORY_FILE}")
        else:
            # 不存在也创建空文件，保证后续流程可用
            _truncate_file(MEMORY_FILE)
            print(f"✅ 已创建并清空 memory：{MEMORY_FILE}")
    else:
        print("⏭️ 跳过：清空 memory（DELETE_MEMORY_CONTENT=False）")

    if DELETE_TEST_OUTPUTS:
        if not TEST_OUTPUTS_DIR.exists():
            print(f"ℹ️ test_outputs 不存在，跳过：{TEST_OUTPUTS_DIR}")
        else:
            removed = 0
            for child in TEST_OUTPUTS_DIR.iterdir():
                # 保留 .gitkeep
                if child.resolve() == KEEP_FILE.resolve():
                    continue
                _remove_tree(child)
                removed += 1
            print(f"✅ 已清理 test_outputs（保留 .gitkeep），删除项数：{removed}")
    else:
        print("⏭️ 跳过：清空 test_outputs（DELETE_TEST_OUTPUTS=False）")

    print("清理完成。")


if __name__ == "__main__":
    main()

