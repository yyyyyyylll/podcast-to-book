"""
开源版的 multi/env.py：

读取 backend/.env 与 single/ 共用即可，无需独立 env 文件。
仍把 STORAGE_DIR 指向 storage/workbench/ 子目录，让多期合书的中间产物
落在专门的子目录，便于和单期 Web 版的产物分开管理。

入口约定：每个 runner 顶部第一行：

    from multi.env import setup
    setup()
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"

_INITIALIZED = False


def setup(*, allow_missing: bool = False) -> None:
    """加载 backend/.env 并把 STORAGE_DIR 切到工作台子目录。"""
    global _INITIALIZED
    if _INITIALIZED:
        return

    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE, override=True)
    elif not allow_missing:
        raise RuntimeError(
            f"\n[multi] 未找到 {_ENV_FILE}\n"
            f"        请复制 {_BACKEND_DIR / '.env.example'} → {_ENV_FILE} 并填写 key。\n"
        )

    # 工作台产物落到 storage/workbench/，便于和单期 Web 版的 storage 分开
    storage_dir = os.environ.get("STORAGE_DIR", "./storage")
    if "workbench" not in storage_dir:
        os.environ["STORAGE_DIR"] = str(_BACKEND_DIR.parent / "storage" / "workbench")

    if str(_BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(_BACKEND_DIR))

    _INITIALIZED = True


def workbench_root() -> Path:
    """返回 storage/workbench 根目录，供 io.py 等使用"""
    setup()
    return Path(os.environ["STORAGE_DIR"])
