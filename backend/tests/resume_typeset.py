"""
补跑排版步骤：从已有 state.json 加载，只执行 typeset_node。

用法：
  cd backend
  python -m tests.resume_typeset <artifact_dir>
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ARTIFACT_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None

if not ARTIFACT_DIR or not (ARTIFACT_DIR / "state.json").exists():
    print(f"用法: python -m tests.resume_typeset <artifact_dir>")
    print(f"  artifact_dir 必须包含 state.json")
    sys.exit(1)


async def run():
    from core.services.llm_service import (
        _current_tracker, UsageTracker, bind_task_id,
    )
    from core.workflow.nodes.typeset import typeset_node

    state = json.loads((ARTIFACT_DIR / "state.json").read_text("utf-8"))
    task_id = state["task_id"]
    title = state.get("title", "unknown")
    bind_task_id(task_id)

    tracker = UsageTracker()
    _current_tracker.set(tracker)

    print(f"[typeset] 开始排版: {title}")
    print(f"[typeset] artifact: {ARTIFACT_DIR.name}")

    t0 = time.perf_counter()
    output = await typeset_node(state)
    state.update(output)
    elapsed = time.perf_counter() - t0

    pdf_path = state.get("pdf_path", "")
    typst_source = state.get("typst_source", "")

    if typst_source:
        (ARTIFACT_DIR / "07_typst_source.typ").write_text(typst_source, encoding="utf-8")

    (ARTIFACT_DIR / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print(f"[typeset] 完成: {title}")
    print(f"[typeset] 耗时: {elapsed:.1f}s")
    if pdf_path and Path(pdf_path).exists():
        size_kb = Path(pdf_path).stat().st_size / 1024
        print(f"[typeset] PDF: {pdf_path} ({size_kb:.0f} KB)")
    else:
        print(f"[typeset] PDF 路径: {pdf_path}")


if __name__ == "__main__":
    asyncio.run(run())
