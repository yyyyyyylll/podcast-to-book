"""
后台任务执行器

在 FastAPI BackgroundTasks 中异步执行 LangGraph 工作流，
过程中实时更新数据库中的任务状态和进度。
支持两级缓存：完全命中直接复制 PDF，仅封面不同只重跑排版。
"""
import asyncio
import os
import random
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, and_

from single.database import async_session
from single.models.task import Task
from core.config import settings
from single.services import credit_service
from single.services.analytics_service import track as track_event
from core.services.llm_service import bind_task_id, clear_llm_logs
from core.workflow.graph import run_workflow_async
from core.workflow.state import create_initial_state
from core.workflow.nodes.transcription import NeedsSpeakerInputError as _NeedsSpeakerInputError

STAGE_PROGRESS = {
    "transcription": 10,
    "compose": 30,
    "enrich": 70,
    "typeset": 93,
}

WORKFLOW_TIMEOUT = 90 * 60  # 90 minutes

_running_tasks: dict[str, asyncio.Task] = {}

# ─── 进度模拟（缓存命中时使用） ──────────────────────────────────

SIMULATED_PROGRESS_FULL = [
    # (累计秒数, stage, progress)
    (0,    "transcription", 5),
    (15,   "transcription", 8),
    (35,   "transcription", 10),
    (60,   "transcription", 13),
    (90,   "transcription", 15),
    (120,  "transcription", 18),
    (155,  "transcription", 22),
    (195,  "transcription", 25),
    (230,  "transcription", 27),
    (265,  "transcription", 28),
    (270,  "compose", 30),
    (290,  "compose", 33),
    (310,  "compose", 36),
    (340,  "compose", 40),
    (370,  "compose", 43),
    (400,  "compose", 45),
    (420,  "compose", 47),
    (425,  "enrich", 50),
    (445,  "enrich", 53),
    (470,  "enrich", 57),
    (500,  "enrich", 62),
    (530,  "enrich", 65),
    (555,  "enrich", 68),
    (570,  "enrich", 70),
    (575,  "typeset", 80),
    (585,  "typeset", 88),
    (595,  "typeset", 93),
    (605,  "typeset", 96),
    (610,  "completed", 100),
]

SIMULATED_PROGRESS_PRE_TYPESET = [
    s for s in SIMULATED_PROGRESS_FULL if s[1] not in ("typeset", "completed")
]


async def cancel_task(task_id: str) -> bool:
    """取消正在运行的后台任务，返回是否成功取消"""
    atask = _running_tasks.get(task_id)
    if atask and not atask.done():
        atask.cancel()
        await _update_task(
            task_id,
            status="failed",
            error_message="管理员手动终止",
        )
        return True
    return False


async def _update_task(task_id: str, **kwargs):
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            for k, v in kwargs.items():
                setattr(task, k, v)
            task.updated_at = datetime.now(timezone.utc)
            await session.commit()


async def _track_failure(task_id: str, user_id: str | None, reason: str, failed_stage: str | None = None):
    try:
        async with async_session() as session:
            await track_event("task_failed", session, user_id=user_id, properties={
                "task_id": task_id,
                "error_type": reason[:100],
                "failed_stage": failed_stage,
            })
            await session.commit()
    except Exception:
        pass


async def _refund_on_failure(task_id: str, user_id: str | None):
    """任务失败时退回积分"""
    if not user_id:
        return
    try:
        async with async_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            cost = task.credit_charged if task and task.credit_charged is not None else 10
            await credit_service.refund(
                session, user_id, cost,
                ref_type="task", ref_id=task_id,
                description="电子书生成失败，退回积分",
            )
            await track_event("credit_refunded", session, user_id=user_id, properties={"task_id": task_id, "amount": cost})
            await session.commit()
        print(f"[background] 已退回 {cost} 积分: user={user_id}, task={task_id}")
    except Exception as e:
        print(f"[background] 退回积分失败: {e}")


async def _simulate_progress(task_id: str, steps: list[tuple]):
    """模拟工作流进度推进，每步加随机抖动让轨迹更自然。"""
    prev_time = 0
    for target_time, stage, progress in steps:
        delay = (target_time - prev_time) * random.uniform(0.85, 1.15)
        if delay > 0:
            await asyncio.sleep(delay)
        await _update_task(task_id, current_stage=stage, progress=progress)
        prev_time = target_time


_MIN_CACHE_SEGMENTS = 10
_MIN_CACHE_DURATION = 120


def _is_task_result_valid(task: Task) -> bool:
    """校验任务的转写结果是否足够充实，排除 mock/无效数据。"""
    trans = task.result_transcription or {}
    segs = trans.get("segments", [])
    dur = trans.get("duration", 0)
    return len(segs) >= _MIN_CACHE_SEGMENTS and dur >= _MIN_CACHE_DURATION


async def _find_cache_source(task: Task):
    """查找 content_hash 匹配的已完成任务，优先匹配 cover_style 完全相同的。"""
    if not task.content_hash:
        return None

    async with async_session() as session:
        # 优先 Level 1：完全匹配（含封面）
        result = await session.execute(
            select(Task).where(and_(
                Task.content_hash == task.content_hash,
                Task.cover_style == task.cover_style,
                Task.status == "completed",
                Task.pdf_path.isnot(None),
                Task.id != task.id,
            )).limit(1)
        )
        exact = result.scalar_one_or_none()
        if exact:
            if not _is_task_result_valid(exact):
                print(f"[background] 缓存源 {exact.id} 转写数据无效，跳过")
            else:
                return exact

        # Level 2：内容相同但封面不同
        result = await session.execute(
            select(Task).where(and_(
                Task.content_hash == task.content_hash,
                Task.status == "completed",
                Task.result_composed.isnot(None),
                Task.id != task.id,
            )).limit(1)
        )
        candidate = result.scalar_one_or_none()
        if candidate and not _is_task_result_valid(candidate):
            print(f"[background] 缓存源 {candidate.id} 转写数据无效，跳过")
            return None
        return candidate


def _copy_pdf(source_task: Task, new_task_id: str) -> str | None:
    """复制源任务的 PDF 到新任务目录，返回新路径；文件不存在返回 None。"""
    if not source_task.pdf_path or not os.path.isfile(source_task.pdf_path):
        return None

    new_dir = Path(settings.STORAGE_DIR) / new_task_id
    new_dir.mkdir(parents=True, exist_ok=True)
    basename = os.path.basename(source_task.pdf_path)
    new_path = str(new_dir / basename)
    shutil.copy2(source_task.pdf_path, new_path)
    return new_path


def _copy_task_assets(source_task_id: str, new_task_id: str):
    """复制插图和封面文件到新任务目录（typeset 编译需要）。"""
    src_dir = Path(settings.STORAGE_DIR) / source_task_id
    dst_dir = Path(settings.STORAGE_DIR) / new_task_id
    dst_dir.mkdir(parents=True, exist_ok=True)

    src_ill = src_dir / "illustrations"
    if src_ill.is_dir():
        dst_ill = dst_dir / "illustrations"
        if dst_ill.exists():
            shutil.rmtree(dst_ill)
        shutil.copytree(src_ill, dst_ill)

    for f in src_dir.glob("cover*"):
        if f.is_file():
            shutil.copy2(f, dst_dir / f.name)

    for f in src_dir.glob("paper_texture*"):
        if f.is_file():
            shutil.copy2(f, dst_dir / f.name)


async def _run_fully_cached(task_id: str, source: Task, user_id: str | None):
    """Level 1：完全缓存命中，复制 PDF + 模拟完整进度。"""
    print(f"[background] 缓存命中 (Level 1): {task_id} ← {source.id}")

    new_pdf = _copy_pdf(source, task_id)
    if not new_pdf:
        return False

    await _update_task(task_id, status="processing", current_stage="transcription", progress=5)
    await _simulate_progress(task_id, SIMULATED_PROGRESS_FULL)

    await _update_task(
        task_id,
        status="completed",
        progress=100,
        current_stage="completed",
        result_transcription=source.result_transcription,
        result_composed=source.result_composed,
        result_highlights=source.result_highlights,
        result_annotated=source.result_annotated,
        result_illustrations=source.result_illustrations,
        result_editor_preface=source.result_editor_preface,
        pdf_path=new_pdf,
        typst_source=source.typst_source,
        usage_stats=source.usage_stats,
        cached_from=source.id,
        completed_at=datetime.now(timezone.utc),
    )

    try:
        async with async_session() as _s:
            await track_event("task_cache_hit", _s, user_id=user_id,
                              properties={"task_id": task_id, "source_id": source.id, "level": "full"})
            await track_event("task_completed", _s, user_id=user_id,
                              properties={"task_id": task_id, "cached": True})
            await _s.commit()
    except Exception:
        pass

    print(f"[background] 缓存任务完成 (Level 1): {task_id}")
    return True


async def _run_typeset_only(task_id: str, source: Task, task_data: dict, user_id: str | None):
    """Level 2：仅封面不同，复制中间结果 + 只跑排版。"""
    print(f"[background] 缓存命中 (Level 2, typeset-only): {task_id} ← {source.id}")

    _copy_task_assets(source.id, task_id)

    await _update_task(
        task_id,
        status="processing",
        current_stage="transcription",
        progress=5,
        result_transcription=source.result_transcription,
        result_composed=source.result_composed,
        result_highlights=source.result_highlights,
        result_annotated=source.result_annotated,
        result_illustrations=source.result_illustrations,
        result_editor_preface=source.result_editor_preface,
    )

    await _simulate_progress(task_id, SIMULATED_PROGRESS_PRE_TYPESET)

    await _update_task(task_id, current_stage="typeset", progress=80)

    from core.workflow.nodes.typeset import typeset_node

    state = {
        "task_id": task_id,
        "title": task_data.get("title", ""),
        "author": task_data.get("author", ""),
        "description": task_data.get("description", ""),
        "host_name": task_data.get("host_name", ""),
        "guest_names": task_data.get("guest_names", []),
        "cover_url": task_data.get("cover_url", ""),
        "cover_style": task_data.get("cover_style", "classic"),
        "podcast_name": task_data.get("podcast_name", ""),
        "podcast_url": task_data.get("podcast_url", ""),
        "episode_title": task_data.get("episode_title", ""),
        "user_title": task_data.get("user_title", ""),
        "user_host_name": task_data.get("user_host_name", ""),
        "user_guest_names": task_data.get("user_guest_names", []),
        "user_editor_preface": task_data.get("user_editor_preface", ""),
        "transcription": source.result_transcription,
        "composed_content": source.result_composed,
        "highlights": source.result_highlights,
        "annotated_content": source.result_annotated,
        "illustrations": source.result_illustrations,
        "editor_preface_content": source.result_editor_preface,
    }

    typeset_result = await asyncio.wait_for(
        typeset_node(state),
        timeout=10 * 60,
    )

    await _update_task(
        task_id,
        status="completed",
        progress=100,
        current_stage="completed",
        pdf_path=typeset_result.get("pdf_path"),
        typst_source=typeset_result.get("typst_source"),
        usage_stats=source.usage_stats,
        cached_from=source.id,
        completed_at=datetime.now(timezone.utc),
    )

    try:
        async with async_session() as _s:
            await track_event("task_cache_hit", _s, user_id=user_id,
                              properties={"task_id": task_id, "source_id": source.id, "level": "typeset_only"})
            await track_event("task_completed", _s, user_id=user_id,
                              properties={"task_id": task_id, "cached": True})
            await _s.commit()
    except Exception:
        pass

    print(f"[background] 缓存任务完成 (Level 2): {task_id}")
    return True


async def run_task(task_id: str):
    """执行完整的工作流并持续更新 DB 状态"""

    # 读取任务信息
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            print(f"[background] 任务不存在: {task_id}")
            return

        user_id = task.user_id
        task_data = {
            "task_id": task.id,
            "audio_path": task.audio_path,
            "title": task.title,
            "author": task.author,
            "description": task.description or "",
            "host_name": task.host_name or "",
            "guest_names": task.guest_names or [],
            "company_names": task.company_names or [],
            "podcast_intro": task.description or "",
            "proper_nouns": task.proper_nouns or [],
            "name_aliases": task.name_aliases or {},
            "cover_url": task.cover_url or "",
            "cover_style": task.cover_style or "swiss",
            "podcast_name": task.podcast_name or "",
            "podcast_url": task.podcast_url or "",
            "episode_title": task.episode_title or "",
            "user_title": task.user_title or "",
            "user_editor_preface": task.editor_preface or "",
            "user_host_name": task.user_host_name or "",
            "user_guest_names": task.user_guest_names or [],
        }

        # ─── 两级缓存检查 ───
        cache_source = await _find_cache_source(task)

    if cache_source:
        try:
            is_exact = cache_source.cover_style == task_data["cover_style"]
            if is_exact and cache_source.pdf_path and os.path.isfile(cache_source.pdf_path):
                ok = await _run_fully_cached(task_id, cache_source, user_id)
                if ok:
                    return
            elif cache_source.result_composed:
                ok = await _run_typeset_only(task_id, cache_source, task_data, user_id)
                if ok:
                    return
        except Exception as e:
            print(f"[background] 缓存路径失败，降级为完整工作流: {e}")
            traceback.print_exc()

    await _update_task(task_id, status="processing", current_stage="transcription", progress=15)

    bind_task_id(task_id)

    try:
        initial_state = create_initial_state(**task_data)

        async def _on_stage(stage: str, progress: int):
            await _update_task(task_id, current_stage=stage, progress=progress)
            try:
                async with async_session() as _s:
                    await track_event("task_stage_changed", _s, user_id=user_id, properties={"task_id": task_id, "stage": stage, "progress": progress})
                    await _s.commit()
            except Exception:
                pass

        final_state = await asyncio.wait_for(
            run_workflow_async(initial_state, on_stage_change=_on_stage),
            timeout=WORKFLOW_TIMEOUT,
        )

        # 保存所有结果
        await _update_task(
            task_id,
            status="completed",
            progress=100,
            current_stage="completed",
            result_transcription=final_state.get("transcription"),
            result_composed=final_state.get("composed_content"),
            result_highlights=final_state.get("highlights"),
            result_annotated=final_state.get("annotated_content"),
            result_illustrations=final_state.get("illustrations"),
            result_editor_preface=final_state.get("editor_preface_content"),
            pdf_path=final_state.get("pdf_path"),
            typst_source=final_state.get("typst_source"),
            usage_stats=final_state.get("usage_stats"),
            completed_at=datetime.now(timezone.utc),
        )
        print(f"[background] 任务完成: {task_id}")

        try:
            async with async_session() as _s:
                total_duration_s = None
                stage_durations = {}
                if final_state.get("usage_stats"):
                    llm_stages = (final_state["usage_stats"].get("llm") or {}).get("stages") or {}
                    for sname, sdata in llm_stages.items():
                        sd = (sdata or {}).get("duration_s", 0)
                        stage_durations[sname] = sd
                    total_duration_s = sum(stage_durations.values())
                page_count = None
                if final_state.get("composed_content") and isinstance(final_state["composed_content"], dict):
                    chapters = final_state["composed_content"].get("chapters") or []
                    page_count = len(chapters)
                await track_event("task_completed", _s, user_id=user_id, properties={
                    "task_id": task_id,
                    "total_duration_s": total_duration_s,
                    "cover_style": task_data.get("cover_style"),
                    "page_count": page_count,
                    "stage_durations": stage_durations,
                })
                await _s.commit()
        except Exception:
            pass

        # 导出素材到小红书 agent
        try:
            from single.services.xhs_exporter import export_to_xhs_agent
            ep_id = await export_to_xhs_agent(task_id)
            if ep_id:
                print(f"[background] 已导出到 xhs-agent: {ep_id}")
        except Exception as export_err:
            print(f"[background] xhs-agent 导出失败（不影响主流程）: {export_err}")

    except asyncio.CancelledError:
        print(f"[background] 任务被终止: {task_id}")
        await _track_failure(task_id, user_id, "cancelled")
        await _refund_on_failure(task_id, user_id)

    except _NeedsSpeakerInputError as nsi:
        print(f"[background] 任务暂停等待用户输入说话人: {task_id}")
        await _update_task(
            task_id,
            status="needs_speaker_input",
            current_stage="transcription",
            progress=12,
            error_message=None,
        )
        async with async_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            t = result.scalar_one_or_none()
            if t:
                t.result_transcription = {
                    "_speaker_input": {
                        "sample_segments": nsi.sample_segments,
                        "speaker_count": nsi.speaker_count,
                        "classify_result": nsi.classify_result,
                    }
                }
                await session.commit()

    except asyncio.TimeoutError:
        print(f"[background] 任务超时: {task_id} (>{WORKFLOW_TIMEOUT}s)")
        await _update_task(
            task_id,
            status="failed",
            error_message="处理超时，请稍后重试",
        )
        await _track_failure(task_id, user_id, "timeout")
        await _refund_on_failure(task_id, user_id)

    except Exception as e:
        traceback.print_exc()
        await _update_task(
            task_id,
            status="failed",
            error_message=str(e)[:2000],
        )
        print(f"[background] 任务失败: {task_id} - {e}")
        await _track_failure(task_id, user_id, str(e)[:200])
        await _refund_on_failure(task_id, user_id)

    finally:
        _running_tasks.pop(task_id, None)


def start_task_in_background(task_id: str):
    """在当前事件循环中启动后台任务"""
    loop = asyncio.get_event_loop()
    atask = loop.create_task(run_task(task_id))
    _running_tasks[task_id] = atask
