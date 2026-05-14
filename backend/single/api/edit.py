"""编辑 API：处理任务编辑数据读取、保存与重新生成"""

import os
import uuid
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select

from core.config import settings
from single.database import async_session
from single.models.task import Task
from single.models.user import User
from single.api.deps import get_current_user
from core.services.cover_service import get_available_styles

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MIME_TO_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


class EditRequest(BaseModel):
    annotated_content: Optional[dict] = None
    highlights: Optional[dict] = None
    illustrations: Optional[dict] = None
    editor_preface: Optional[str] = None
    user_title: Optional[str] = None
    user_host_name: Optional[str] = None
    user_guest_names: Optional[List[str]] = None
    cover_style: Optional[str] = None
    custom_cover_url: Optional[str] = None
    custom_full_cover_url: Optional[str] = None
    custom_back_cover_url: Optional[str] = None
    restore_original_cover: Optional[bool] = None


def _validate_task_for_edit(task: Task | None, user: User, *, require_full_access: bool = False):
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    if task.status == "regenerating":
        raise HTTPException(status_code=409, detail="任务正在重新生成，请稍后再试")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成，暂不支持编辑")
    if require_full_access:
        unlock = getattr(task, "unlock_level", None) or "full_text"
        if unlock != "full_access":
            raise HTTPException(status_code=403, detail="该任务未解锁完整编辑权限")


@router.get("/tasks/{task_id}/edit-data")
async def get_edit_data(task_id: str, user: User = Depends(get_current_user)):
    """获取任务的编辑数据"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        _validate_task_for_edit(task, user)

        illustrations = task.result_illustrations or {"images": []}
        images_with_preview = []
        for img in illustrations.get("images", []):
            fn = img.get("filename", "")
            images_with_preview.append({
                **img,
                "preview_url": f"/files/{task_id}/{fn}" if fn else "",
            })

        cover_styles = get_available_styles()
        previews_dir = Path(settings.STORAGE_DIR) / task_id / "cover_previews"
        styles_with_preview = []
        for s in cover_styles:
            sid = s.get("id", "")
            preview_file = previews_dir / f"{sid}.png"
            styles_with_preview.append({
                "id": sid,
                "name": s.get("name", sid),
                "preview_url": f"/files/{task_id}/cover_previews/{sid}.png" if preview_file.exists() else "",
            })

        annotated = task.result_annotated or task.result_composed or {}
        original_title = annotated.get("title", task.title)

        # 兼容历史任务：若未记录 original_cover_url，则把当前 cover_url 回填进去
        orig_cover = task.original_cover_url or ""
        if not orig_cover:
            cur = task.cover_url or ""
            if cur.startswith("http://") or cur.startswith("https://"):
                orig_cover = cur
                task.original_cover_url = cur
                await session.commit()

        return {
            "annotated_content": task.result_annotated,
            "highlights": task.result_highlights or {"quotes": []},
            "illustrations": {**illustrations, "images": images_with_preview},
            "editor_preface": task.result_editor_preface or task.editor_preface or "",
            "user_title": task.user_title or "",
            "user_host_name": task.user_host_name or "",
            "user_guest_names": task.user_guest_names or [],
            "host_name": task.host_name or "",
            "guest_names": task.guest_names or [],
            "cover_style": task.cover_style or "classic",
            "custom_full_cover_url": task.custom_full_cover_url or "",
            "custom_back_cover_url": task.custom_back_cover_url or "",
            "original_cover_url": orig_cover,
            "available_cover_styles": styles_with_preview,
            "original_title": original_title,
            "task_title": task.title,
        }


@router.patch("/tasks/{task_id}/edit")
async def edit_and_regenerate(
    task_id: str,
    req: EditRequest,
    user: User = Depends(get_current_user),
):
    """保存编辑并重新生成 PDF"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        _validate_task_for_edit(task, user, require_full_access=True)

        task.status = "regenerating"

        if req.annotated_content is not None:
            task.result_annotated = req.annotated_content
        if req.highlights is not None:
            task.result_highlights = req.highlights
        if req.illustrations is not None:
            clean_images = []
            for img in req.illustrations.get("images", []):
                clean_images.append({
                    k: v for k, v in img.items() if k != "preview_url"
                })
            task.result_illustrations = {**req.illustrations, "images": clean_images}
        if req.editor_preface is not None:
            task.result_editor_preface = req.editor_preface
            task.editor_preface = req.editor_preface
        if req.user_title is not None:
            task.user_title = req.user_title
        if req.user_host_name is not None:
            task.user_host_name = req.user_host_name
        if req.user_guest_names is not None:
            task.user_guest_names = req.user_guest_names
        if req.cover_style is not None:
            task.cover_style = req.cover_style
        # 首次编辑且尚未记录原始封面时，用当前 cover_url 回填
        if not task.original_cover_url:
            cur = task.cover_url or ""
            if cur.startswith("http://") or cur.startswith("https://"):
                task.original_cover_url = cur
        if req.restore_original_cover:
            if task.original_cover_url:
                task.cover_url = task.original_cover_url
            task.custom_full_cover_url = ""
            task.custom_back_cover_url = ""
        else:
            if req.custom_cover_url is not None:
                task.cover_url = req.custom_cover_url
                task.custom_full_cover_url = ""
            if req.custom_full_cover_url is not None:
                task.custom_full_cover_url = req.custom_full_cover_url
            if req.custom_back_cover_url is not None:
                task.custom_back_cover_url = req.custom_back_cover_url

        task.edit_count = (task.edit_count or 0) + 1

        await session.commit()

        task_snapshot = {
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

    try:
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
            if not task:
                raise HTTPException(status_code=404, detail="任务在重新生成过程中丢失")
            task.pdf_path = pdf_path
            task.typst_source = typst_source
            task.status = "completed"
            task.error_message = None
            task.updated_at = datetime.now(timezone.utc)
            await session.commit()

        # 书稿内容已变，旧的 book.epub 必须失效，否则用户再下 EPUB 会拿到编辑前的版本。
        # 下次调用 GET /tasks/{id}/download/epub 时会自动重新生成。
        from single.services.epub_service import invalidate_epub_cache
        invalidate_epub_cache(task_id)

        pdf_url = f"/files/{task_id}/{os.path.basename(pdf_path)}" if pdf_path else None
        return {"status": "completed", "pdf_url": pdf_url}

    except Exception as e:
        error_msg = f"重新生成失败: {str(e)}"
        async with async_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "completed"
                task.error_message = error_msg
                await session.commit()

        raise HTTPException(
            status_code=500,
            detail=f"重新生成失败: {str(e)}，已回退到原 PDF",
        )


@router.post("/tasks/{task_id}/illustrations/upload")
async def upload_illustration(
    task_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传用户自定义插图"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        _validate_task_for_edit(task, user)

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")

    ill_dir = Path(settings.STORAGE_DIR) / task_id / "illustrations"
    ill_dir.mkdir(parents=True, exist_ok=True)

    ext = MIME_TO_EXT.get(file.content_type, ".jpg")
    filename = f"user_upload_{uuid.uuid4().hex[:8]}{ext}"
    filepath = ill_dir / filename

    with open(filepath, "wb") as f:
        f.write(content)

    return {"filename": f"illustrations/{filename}"}


@router.post("/tasks/{task_id}/cover/upload")
async def upload_custom_cover(
    task_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传自定义封面图片"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        _validate_task_for_edit(task, user)

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")

    task_dir = Path(settings.STORAGE_DIR) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    ext = MIME_TO_EXT.get(file.content_type, ".png")
    filename = f"custom_cover{ext}"
    filepath = task_dir / filename

    with open(filepath, "wb") as f:
        f.write(content)

    return {"cover_url": f"/files/{task_id}/{filename}"}


@router.post("/tasks/{task_id}/full-cover/upload")
async def upload_full_cover(
    task_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传整体封面图片（跳过模板，直接使用用户上传的整张封面）"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        _validate_task_for_edit(task, user)

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")

    task_dir = Path(settings.STORAGE_DIR) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    ext = MIME_TO_EXT.get(file.content_type, ".png")
    filename = f"full_cover{ext}"
    filepath = task_dir / filename

    with open(filepath, "wb") as f:
        f.write(content)

    return {"full_cover_url": f"/files/{task_id}/{filename}"}


@router.post("/tasks/{task_id}/back-cover/upload")
async def upload_back_cover(
    task_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传自定义封底图片"""
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        _validate_task_for_edit(task, user)

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")

    task_dir = Path(settings.STORAGE_DIR) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    ext = MIME_TO_EXT.get(file.content_type, ".png")
    filename = f"back_cover{ext}"
    filepath = task_dir / filename

    with open(filepath, "wb") as f:
        f.write(content)

    return {"back_cover_url": f"/files/{task_id}/{filename}"}
