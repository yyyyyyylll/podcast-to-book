"""
从数据库复用已有内容，重跑 illustration + typeset，得到一本"完整"的样书。

与 run_full_pipeline 的区别：
- 跳过 ASR / compose / extraction / annotation / editor_preface（全部复用 DB 里的产物）
- 只重跑 illustration（26 张插图的文件已丢失，必须重生成）
- 跑最新版 typeset（新的封面/页脚/字体/版式）
- 默认 **不写回数据库**，产物只落在 `backend/storage/<task_id>/`

用法：
  cd backend
  python -m tests.retypeset_full_from_db <task_id>

示例：
  python -m tests.retypeset_full_from_db deec3a9a-4b29-43e0-bd2d-8f4a79107300
"""
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select

from single.database import async_session
from single.models.task import Task


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[retypeset {ts}] {msg}", flush=True)


async def _load_task(task_id: str) -> Task:
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise SystemExit(f"task {task_id} not found in DB")
        return task


def _build_state(task: Task) -> dict:
    """用 DB 里的已有产物构造 typeset/illustration 需要的 state。"""
    composed = task.result_composed or {}
    content_type = (composed.get("content_type") or "business").strip() or "business"

    return {
        "task_id": task.id,
        "audio_path": "",
        "title": task.title or "",
        "author": task.author or "",
        "description": task.description or "",
        "host_name": task.host_name or "",
        "guest_names": list(task.guest_names or []),
        "company_names": list(task.company_names or []),
        "proper_nouns": list(task.proper_nouns or []),
        "name_aliases": task.name_aliases or {},
        "podcast_intro": task.description or "",
        "cover_url": task.cover_url or "",
        "original_cover_url": task.original_cover_url or "",
        "cover_style": task.cover_style or "classic",
        "custom_full_cover_url": task.custom_full_cover_url or "",
        "custom_back_cover_url": task.custom_back_cover_url or "",
        "podcast_name": task.podcast_name or "",
        "podcast_url": task.podcast_url or "",
        "publish_date": "",
        "episode_title": task.episode_title or "",
        "user_title": task.user_title or "",
        "user_host_name": task.user_host_name or "",
        "user_guest_names": list(task.user_guest_names or []),
        "user_editor_preface": task.editor_preface or "",
        "content_type": content_type,
        # DB 中已有的中间产物，全部复用
        "transcription": task.result_transcription,
        "composed_content": task.result_composed,
        "highlights": task.result_highlights or {"quotes": []},
        "annotated_content": task.result_annotated,
        "editor_preface_content": task.result_editor_preface or "",
        # 插图 / PDF 这两步的旧产物丢了，占位即可
        "illustrations": None,
        "pdf_path": None,
        "typst_source": None,
    }


async def run(task_id: str) -> None:
    from core.services.llm_service import (
        _current_tracker, UsageTracker, bind_task_id,
    )
    from core.workflow.nodes.illustration import illustration_node
    from core.workflow.nodes.typeset import typeset_node

    t_all = time.perf_counter()

    _log("=" * 64)
    _log(f"重跑 illustration + typeset（复用 DB 其他步骤产物）")
    _log(f"task_id: {task_id}")
    _log("=" * 64)

    task = await _load_task(task_id)
    _log(f"加载 task: {task.title}")
    _log(f"  podcast: {task.podcast_name}")
    _log(f"  status:  {task.status}")

    state = _build_state(task)

    composed = state["composed_content"] or {}
    annotated = state["annotated_content"] or {}
    highlights = state["highlights"] or {}
    _log(f"复用 DB 数据: composed 章节 {len(composed.get('chapters', []))} / "
         f"annotated 章节 {len(annotated.get('chapters', []))} / "
         f"金句 {len(highlights.get('quotes', []))} / "
         f"编者序 {len(state['editor_preface_content'] or '')} 字")

    bind_task_id(task_id)
    tracker = UsageTracker()
    _current_tracker.set(tracker)

    # ── Step A: 插图 ──────────────────────────────
    _log("")
    _log("── Step A: illustration（双路径：AI 生图 + 网络搜图）──")
    tracker.set_stage("illustration")
    t0 = time.perf_counter()
    output = await illustration_node(state)
    state.update(output)
    t_illust = time.perf_counter() - t0

    illust = state.get("illustrations") or {}
    images = illust.get("images", [])
    sources = {}
    for img in images:
        src = img.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    _log(
        f"插图完成 ({t_illust:.1f}s): "
        f"通过 {illust.get('total_count', len(images))}, "
        f"生成 {illust.get('generated_count', 0)}, "
        f"拒绝 {illust.get('rejected_count', 0)}"
    )
    if sources:
        _log(f"  来源分布: {sources}")

    # ── Step B: 排版 ──────────────────────────────
    _log("")
    _log("── Step B: typeset（最新排版逻辑）──")
    tracker.set_stage("typeset")
    t0 = time.perf_counter()
    output = await typeset_node(state)
    state.update(output)
    t_typeset = time.perf_counter() - t0

    pdf_path = state.get("pdf_path", "")
    typst_source = state.get("typst_source", "") or ""
    _log(f"排版完成 ({t_typeset:.1f}s)")
    if pdf_path and Path(pdf_path).exists():
        size_kb = Path(pdf_path).stat().st_size / 1024
        _log(f"  PDF: {pdf_path} ({size_kb:.0f} KB)")
    _log(f"  typst_source: {len(typst_source)} 字符")

    elapsed = time.perf_counter() - t_all
    _log("")
    _log("=" * 64)
    _log(f"全部完成，总耗时 {elapsed:.1f}s ({elapsed/60:.1f}min)")
    _log(f"  · illustration: {t_illust:.1f}s")
    _log(f"  · typeset:      {t_typeset:.1f}s")
    _log(f"产物目录: {Path(pdf_path).parent if pdf_path else '(未知)'}")
    _log("")
    _log("数据库未写回（typst_source / pdf_path / result_illustrations 保持原样）。")
    _log("如需同步到 DB，请告诉我。")
    _log("=" * 64)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m tests.retypeset_full_from_db <task_id>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))
