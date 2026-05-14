"""
工作台 IO 工具：作业目录、JSON 读写、slug 生成。

约定的目录结构：

    storage/workbench/jobs/<job_id>/
      show.json                  ← 节目级元数据
      episodes_index.json        ← 单集 eid 列表（顺序 = 章节序）
      episodes/
        ep01/
          meta.json              ← 单集元数据 + 音频 URL
          transcript.json        ← 转写产物（runner 后续填充）
          compose.json
          ...
      merged/
        unified_glossary.json    ← 跨集统一术语表
        merged_chapters.json
        final_book.epub
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from multi.env import workbench_root


def slugify(text: str, *, max_len: int = 40) -> str:
    """生成文件系统安全的 slug，保留中英文。"""
    text = (text or "").strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\-]", "", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return (text or "untitled")[:max_len]


@dataclass
class JobPaths:
    """单个作业的所有路径锚点"""
    job_id: str
    root: Path

    @classmethod
    def create(cls, job_id: str | None = None, *, hint: str = "") -> "JobPaths":
        if not job_id:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            job_id = f"{slugify(hint)}-{ts}" if hint else ts
        else:
            job_id = slugify(job_id, max_len=80)
        root = workbench_root() / "jobs" / job_id
        root.mkdir(parents=True, exist_ok=True)
        return cls(job_id=job_id, root=root)

    @classmethod
    def open(cls, job_id: str) -> "JobPaths":
        root = workbench_root() / "jobs" / job_id
        if not root.exists():
            raise FileNotFoundError(f"作业目录不存在: {root}")
        return cls(job_id=job_id, root=root)

    @property
    def show_json(self) -> Path:
        return self.root / "show.json"

    @property
    def episodes_index_json(self) -> Path:
        return self.root / "episodes_index.json"

    @property
    def episodes_dir(self) -> Path:
        d = self.root / "episodes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def merged_dir(self) -> Path:
        d = self.root / "merged"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def episode_dir(self, idx: int) -> Path:
        d = self.episodes_dir / f"ep{idx:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dict__"):
        return {k: v for k, v in o.__dict__.items() if not k.startswith("_")}
    return str(o)
