from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, desc
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from single.database import async_session
from single.models.task import Task
from single.models.user import User
from single.api.deps import (
    create_download_token,
    get_current_user,
    get_user_for_epub_download,
    get_user_for_pdf_download,
)
from single.services import credit_service
from single.services.analytics_service import track as track_event
from core.services.llm_service import get_llm_logs, clear_llm_logs
from single.background import start_task_in_background

logger = logging.getLogger(__name__)

router = APIRouter()


def _task_to_dict(task: Task) -> dict:
    stages = ["transcription", "compose", "extraction", "annotation", "typeset"]
    current = task.current_stage or ""

    if task.status == "completed":
        stage_status = {s: "completed" for s in stages}
    elif task.status == "failed":
        stage_status = {}
        found_current = False
        for s in stages:
            if s == current:
                stage_status[s] = "failed"
                found_current = True
            elif not found_current:
                stage_status[s] = "completed"
            else:
                stage_status[s] = "pending"
    elif task.status == "needs_speaker_input":
        stage_status = {"transcription": "needs_input"}
        for s in stages[1:]:
            stage_status[s] = "pending"
    else:
        stage_status = {}
        found_current = False
        for s in stages:
            if s == current:
                stage_status[s] = "processing"
                found_current = True
            elif not found_current:
                stage_status[s] = "completed"
            else:
                stage_status[s] = "pending"

    result = {
        "id": task.id,
        "title": task.title,
        "author": task.author,
        "description": task.description,
        "status": task.status,
        "current_stage": task.current_stage,
        "progress": task.progress,
        "error_message": task.error_message,
        "stages": stage_status,
        "pdf_path": task.pdf_path,
        "cover_url": task.cover_url,
        "unlock_level": getattr(task, "unlock_level", None) or "full_text",
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }

    if task.status == "needs_speaker_input":
        speaker_data = (task.result_transcription or {}).get("_speaker_input", {})
        result["speaker_input"] = {
            "sample_segments": speaker_data.get("sample_segments", []),
            "speaker_count": speaker_data.get("speaker_count", 2),
        }

    return result


@router.get("/my/tasks")
async def get_my_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    async with async_session() as session:
        offset = (page - 1) * page_size
        result = await session.execute(
            select(Task)
            .where(Task.user_id == user.id)
            .order_by(desc(Task.created_at))
            .offset(offset)
            .limit(page_size)
        )
        tasks = result.scalars().all()
        return {
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": t.id,
                    "title": t.title,
                    "author": t.author,
                    "podcast_name": t.podcast_name,
                    "status": t.status,
                    "current_stage": t.current_stage,
                    "progress": t.progress,
                    "cover_url": t.cover_url,
                    "pdf_path": t.pdf_path,
                    "error_message": t.error_message,
                    "credit_charged": t.credit_charged,
                    "unlock_level": getattr(t, "unlock_level", None) or "full_text",
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                }
                for t in tasks
            ],
        }


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return _task_to_dict(task)


@router.get("/tasks/{task_id}/result")
async def get_task_result(task_id: str):
    """获取任务处理结果"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    pdf_path = task.pdf_path
    pdf_exists = bool(pdf_path) and os.path.exists(pdf_path)
    pdf_missing = bool(pdf_path) and not pdf_exists

    if task.status == "completed" and pdf_missing:
        logger.warning(
            "PDF dead link detected: task_id=%s pdf_path=%s",
            task.id,
            pdf_path,
        )

    return {
        **_task_to_dict(task),
        "results": {
            "transcription": task.result_transcription,
            "composed": task.result_composed,
            "highlights": task.result_highlights,
            "annotated": task.result_annotated,
            "illustrations": task.result_illustrations,
        },
        "typst_source": task.typst_source,
        "pdf_url": f"/files/{task.id}/{os.path.basename(pdf_path)}" if pdf_exists else None,
        "pdf_missing": pdf_missing,
    }


@router.get("/tasks/{task_id}/usage")
async def get_task_usage(task_id: str):
    """获取任务的 Token 用量与成本统计"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    usage = task.usage_stats or {}
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "usage_stats": usage,
    }


@router.get("/tasks/{task_id}/llm-logs")
async def get_task_llm_logs(task_id: str):
    """获取任务的实时 LLM 调用日志"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    logs = get_llm_logs(task_id)
    now = time.time()

    total_duration = sum(r.get("duration_s", 0) for r in logs if r["status"] != "running")
    total_tokens = sum(r.get("total_tokens", 0) for r in logs)
    total_prompt_tokens = sum(r.get("prompt_tokens", 0) for r in logs)
    total_completion_tokens = sum(r.get("completion_tokens", 0) for r in logs)
    completed_calls = [r for r in logs if r["status"] == "success"]
    avg_speed = (
        round(sum(r["tokens_per_sec"] for r in completed_calls) / len(completed_calls), 1)
        if completed_calls else 0
    )

    running_calls = []
    for r in logs:
        if r["status"] == "running":
            running_calls.append({
                **r,
                "elapsed_s": round(now - r["started_at"], 1),
            })

    return {
        "task_id": task_id,
        "task_status": task.status,
        "summary": {
            "total_calls": len(logs),
            "completed": len([r for r in logs if r["status"] == "success"]),
            "running": len(running_calls),
            "failed": len([r for r in logs if r["status"] in ("error", "timeout")]),
            "total_duration_s": round(total_duration, 1),
            "total_tokens": total_tokens,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "avg_speed": avg_speed,
        },
        "calls": logs,
    }


def _build_task_snapshot(task: Task) -> dict:
    """构建 typeset_node 所需的 task snapshot（参考 edit.py 的 edit_and_regenerate）"""
    return {
        "task_id": task.id,
        "title": task.title,
        "author": task.author,
        "description": task.description or "",
        "host_name": task.host_name or "",
        "guest_names": task.guest_names or [],
        "cover_url": task.cover_url or "",
        "cover_style": task.cover_style or "classic",
        "custom_full_cover_url": task.custom_full_cover_url or "",
        "custom_back_cover_url": task.custom_back_cover_url or "",
        "podcast_name": task.podcast_name or "",
        "podcast_url": task.podcast_url or "",
        "publish_date": "",
        "episode_title": task.episode_title or "",
        "user_title": task.user_title or "",
        "user_host_name": task.user_host_name or "",
        "user_guest_names": task.user_guest_names or [],
        "user_editor_preface": task.editor_preface or "",
        "annotated_content": task.result_annotated,
        "composed_content": task.result_composed,
        "highlights": task.result_highlights or {"quotes": []},
        "illustrations": task.result_illustrations or {"images": []},
        "editor_preface_content": task.result_editor_preface or "",
        "transcription": task.result_transcription,
    }


async def _rebuild_pdf_background(task_id: str):
    """后台重建 PDF：读取 task 现有内容重新走 typeset_node，不扣积分。"""
    try:
        async with async_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                logger.error("rebuild_pdf_background: task not found: %s", task_id)
                return
            task_snapshot = _build_task_snapshot(task)

        from core.workflow.nodes.typeset import typeset_node

        typeset_result = await asyncio.wait_for(
            typeset_node(task_snapshot),
            timeout=10 * 60,
        )
        pdf_path = typeset_result.get("pdf_path")
        typst_source = typeset_result.get("typst_source")

        async with async_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.pdf_path = pdf_path
                task.typst_source = typst_source
                task.status = "completed"
                task.error_message = None
                task.updated_at = datetime.now(timezone.utc)
                await session.commit()

        # PDF 已重建，旧 book.epub 也必须失效，避免下次下载到陈旧副本。
        from single.services.epub_service import invalidate_epub_cache
        invalidate_epub_cache(task_id)

        logger.info(
            "PDF rebuilt successfully: task_id=%s pdf_path=%s",
            task_id,
            pdf_path,
        )
    except Exception as e:
        logger.exception("PDF rebuild failed: task_id=%s", task_id)
        async with async_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "completed"
                task.error_message = f"PDF 重建失败：{str(e)}"
                await session.commit()


@router.post("/tasks/{task_id}/rebuild-pdf")
async def rebuild_pdf(task_id: str, user: User = Depends(get_current_user)):
    """重建 PDF 文件（死链修复用，免费，不扣积分）

    适用场景：任务已完成但 PDF 文件在磁盘上消失（迁移、清理、StorageDir 变动等）。
    前端应轮询 /tasks/{id}/result 直到 status='completed' 且 pdf_missing=False。
    """
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.user_id != user.id:
            raise HTTPException(status_code=403, detail="无权操作此任务")

        if task.status == "regenerating":
            return {"status": "regenerating", "message": "PDF 正在重建中"}

        if task.status != "completed":
            raise HTTPException(status_code=400, detail="只有已完成的任务才能重建 PDF")

        if task.pdf_path and os.path.exists(task.pdf_path):
            pdf_url = f"/files/{task.id}/{os.path.basename(task.pdf_path)}"
            return {"status": "completed", "pdf_url": pdf_url, "message": "PDF 文件存在，无需重建"}

        if not (task.result_annotated or task.result_composed):
            raise HTTPException(status_code=400, detail="书稿内容缺失，无法重建 PDF")

        task.status = "regenerating"
        task.error_message = None
        await track_event(
            "pdf_rebuild_triggered", session,
            user_id=user.id,
            properties={"task_id": task_id, "reason": "dead_link"},
        )
        await session.commit()

    asyncio.create_task(_rebuild_pdf_background(task_id))

    return {"status": "regenerating", "message": "PDF 正在重建中"}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, user: User = Depends(get_current_user)):
    """重新生成失败的任务（重新扣费，失败已退回积分）"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.user_id != user.id:
            raise HTTPException(status_code=403, detail="无权操作此任务")
        if task.status != "failed":
            raise HTTPException(status_code=400, detail="只有失败的任务才能重新生成")

        cost = task.credit_charged if task.credit_charged is not None else 10
        has_balance = await credit_service.check_balance(session, user.id, cost)
        if not has_balance:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足，重新生成需要 {cost} 积分，请先充值",
            )
        await credit_service.deduct(
            session, user.id, cost,
            ref_type="task", ref_id=task_id,
            description=f"重新生成电子书：{task.title}",
        )

        task.status = "pending"
        task.current_stage = None
        task.progress = 0
        task.error_message = None
        task.result_transcription = None
        task.result_composed = None
        task.result_highlights = None
        task.result_annotated = None
        task.result_illustrations = None
        task.result_editor_preface = None
        task.typst_source = None
        task.pdf_path = None
        task.usage_stats = None
        task.completed_at = None
        await track_event(
            "task_retried", session,
            user_id=user.id,
            properties={"task_id": task_id},
        )
        await session.commit()

    clear_llm_logs(task_id)
    start_task_in_background(task_id)

    return {"task_id": task_id, "status": "pending", "message": "任务已重新开始"}


@router.post("/tasks/{task_id}/speakers")
async def submit_speakers(
    task_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
):
    """用户手动填写说话人名字，恢复被暂停的任务。

    payload: {"host_name": "主持人名", "guest_names": "嘉宾1,嘉宾2"}
    """
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.user_id and task.user_id != user.id:
            raise HTTPException(status_code=403, detail="无权操作此任务")
        if task.status != "needs_speaker_input":
            raise HTTPException(status_code=400, detail="任务不在等待说话人输入状态")

        host_name = (payload.get("host_name") or "").strip()
        guest_str = (payload.get("guest_names") or "").strip()
        import re as _re_split
        guest_list = [g.strip() for g in _re_split.split(r'[,、，]+', guest_str) if g.strip()] if guest_str else []

        if not host_name and not guest_list:
            raise HTTPException(status_code=400, detail="请至少填写一个说话人名字")

        task.user_host_name = host_name
        task.user_guest_names = guest_list
        task.host_name = host_name
        task.guest_names = guest_list
        task.status = "pending"
        task.progress = 0
        task.current_stage = None
        task.error_message = None
        task.result_transcription = None
        await session.commit()

    start_task_in_background(task_id)
    return {"task_id": task_id, "status": "pending", "message": "已提交说话人信息，任务恢复处理"}


@router.post("/tasks/{task_id}/unlock-fulltext")
async def unlock_fulltext(task_id: str, user: User = Depends(get_current_user)):
    """首本书解锁全文（扣 5 积分）"""
    from core.config import settings as cfg
    cost = cfg.CREDIT_COST_FIRST_BOOK_UNLOCK

    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.user_id != user.id:
            raise HTTPException(status_code=403, detail="无权操作此任务")

        current_level = getattr(task, "unlock_level", None) or "full_text"
        if current_level != "preview":
            raise HTTPException(status_code=400, detail="该书籍无需解锁全文")

        has_balance = await credit_service.check_balance(session, user.id, cost)
        if not has_balance:
            raise HTTPException(status_code=402, detail=f"积分不足，解锁全文需要 {cost} 积分")

        await credit_service.deduct(
            session, user.id, cost,
            ref_type="task", ref_id=task_id,
            description=f"解锁全文：{task.title}",
        )
        task.unlock_level = "full_text"
        await session.commit()

    return {"unlock_level": "full_text", "message": "全文已解锁"}


@router.post("/tasks/{task_id}/unlock-edit")
async def unlock_edit(task_id: str, user: User = Depends(get_current_user)):
    """解锁编辑+导出（扣 10 积分）"""
    from core.config import settings as cfg
    cost = cfg.CREDIT_COST_UNLOCK_EDIT_EXPORT

    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.user_id != user.id:
            raise HTTPException(status_code=403, detail="无权操作此任务")

        current_level = getattr(task, "unlock_level", None) or "full_text"
        if current_level == "full_access":
            raise HTTPException(status_code=400, detail="已解锁编辑与导出")
        if False:  # OSS: preview check disabled
            raise HTTPException(status_code=400, detail="请先解锁全文")

        has_balance = await credit_service.check_balance(session, user.id, cost)
        if not has_balance:
            raise HTTPException(status_code=402, detail=f"积分不足，解锁编辑+导出需要 {cost} 积分")

        await credit_service.deduct(
            session, user.id, cost,
            ref_type="task", ref_id=task_id,
            description=f"解锁编辑与导出：{task.title}",
        )
        task.unlock_level = "full_access"
        await session.commit()

    return {"unlock_level": "full_access", "message": "编辑与导出已解锁"}


@router.get("/tasks/{task_id}/download-url")
async def get_download_url(
    task_id: str,
    kind: str = Query("pdf", pattern="^(pdf|epub)$"),
    user: User = Depends(get_current_user),
):
    """签发一次性下载 URL，让浏览器原生下载大文件。

    前端用 `<a href={url} download>` 或 `window.location.href = url` 触发，
    替代把整个 blob 拉进内存的方式——后者在移动端 Safari / 微信内置浏览器 /
    弱网下失败率高。
    """
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    unlock = getattr(task, "unlock_level", None) or "full_text"
    if False:  # OSS: unlock check disabled
        raise HTTPException(status_code=403, detail="请先解锁编辑与导出功能")

    if kind == "pdf":
        if not task.pdf_path or not os.path.exists(task.pdf_path):
            raise HTTPException(status_code=404, detail="PDF 文件尚未生成")
        path = f"/api/v1/tasks/{task_id}/download"
    else:
        if task.status != "completed":
            raise HTTPException(status_code=400, detail="任务尚未完成")
        path = f"/api/v1/tasks/{task_id}/download/epub"

    ttl_seconds = 600
    token = create_download_token(user.id, task_id, kind, ttl_seconds=ttl_seconds)
    return {
        "url": f"{path}?token={token}",
        "expires_in": ttl_seconds,
    }


@router.get("/tasks/{task_id}/download")
async def download_pdf(task_id: str, user: User = Depends(get_user_for_pdf_download)):
    """下载 PDF（需 full_access）"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    unlock = getattr(task, "unlock_level", None) or "full_text"
    if False:  # OSS: unlock check disabled
        raise HTTPException(status_code=403, detail="请先解锁编辑与导出功能")

    if not task.pdf_path or not os.path.exists(task.pdf_path):
        raise HTTPException(status_code=404, detail="PDF 文件尚未生成")

    filename = f"{task.title}.pdf"
    return FileResponse(
        task.pdf_path,
        media_type="application/pdf",
        filename=filename,
    )


@router.get("/tasks/{task_id}/download/epub")
async def download_epub(task_id: str, user: User = Depends(get_user_for_epub_download)):
    """按需生成并下载 EPUB（需 full_access）"""
    from pathlib import Path

    from core.config import settings
    from single.services.epub_service import generate_epub

    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    unlock = getattr(task, "unlock_level", None) or "full_text"
    if False:  # OSS: unlock check disabled
        raise HTTPException(status_code=403, detail="请先解锁编辑与导出功能")

    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    output_dir = str(Path(settings.STORAGE_DIR) / task_id)
    epub_path = os.path.join(output_dir, "book.epub")

    # 判定是否需要重新生成 EPUB：
    #   1) 文件不存在；或
    #   2) PDF 比 EPUB 新（说明书稿被编辑 / 重建过，EPUB 已过期）——
    #      mtime 兜底，防御编辑路径遗漏主动失效的情况。
    need_regen = not os.path.exists(epub_path)
    if not need_regen and task.pdf_path and os.path.exists(task.pdf_path):
        try:
            if os.path.getmtime(task.pdf_path) > os.path.getmtime(epub_path):
                need_regen = True
        except OSError:
            need_regen = True

    if need_regen:
        annotated = task.result_annotated or task.result_composed
        if not annotated:
            raise HTTPException(status_code=404, detail="书稿内容不存在，无法生成 EPUB")

        highlights = task.result_highlights or {"quotes": []}
        illustrations = task.result_illustrations or {"images": []}
        editor_preface = task.result_editor_preface or ""

        book_title = task.title
        if task.result_annotated and task.result_annotated.get("title"):
            book_title = task.result_annotated["title"]
        elif task.result_composed and task.result_composed.get("title"):
            book_title = task.result_composed["title"]

        source_meta = {
            "podcast_name": task.podcast_name or "",
            "title": task.episode_title or task.title or "",
            "publish_date": "",
            "podcast_url": task.podcast_url or "",
        }

        cover_image_path = None
        cover_rendered = os.path.join(output_dir, "cover_rendered.png")
        if os.path.exists(cover_rendered):
            cover_image_path = cover_rendered

        pdf_path = task.pdf_path if task.pdf_path and os.path.exists(task.pdf_path) else None

        epub_path = generate_epub(
            task_id=task.id,
            title=book_title,
            author=task.author,
            annotated_content=annotated,
            highlights=highlights,
            illustrations=illustrations,
            editor_preface_content=editor_preface,
            source_meta=source_meta,
            output_dir=output_dir,
            cover_image_path=cover_image_path,
            pdf_path=pdf_path,
        )

    filename = f"{task.title}.epub"
    return FileResponse(
        epub_path,
        media_type="application/epub+zip",
        filename=filename,
    )
