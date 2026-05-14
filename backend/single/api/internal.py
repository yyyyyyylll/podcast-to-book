"""
内部 API：供 xhs-ops 等外部服务调用。
通过 X-Internal-Key 头进行 API Key 鉴权。
"""

import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, desc, func

from core.config import settings
from single.database import async_session
from single.models.task import Task

router = APIRouter()

SUPPORTED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _check_key(x_internal_key: str | None):
    key = settings.INTERNAL_API_KEY
    if not key:
        raise HTTPException(status_code=503, detail="内部 API 未启用")
    if x_internal_key != key:
        raise HTTPException(status_code=401, detail="API Key 无效")


def _quote_preview(task: Task, max_items: int = 3) -> list[str]:
    highlights = task.result_highlights or {}
    quotes = highlights.get("quotes", [])
    return [q.get("text", "")[:80] for q in quotes[:max_items]]


# ─── 素材列表 ───────────────────────────────────────────────

@router.get("/materials")
async def list_materials(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    x_internal_key: str | None = Header(None),
):
    _check_key(x_internal_key)
    async with async_session() as session:
        count_q = await session.execute(
            select(func.count(Task.id)).where(Task.status == "completed")
        )
        total = count_q.scalar() or 0

        result = await session.execute(
            select(Task)
            .where(Task.status == "completed")
            .order_by(desc(Task.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        tasks = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": t.id,
                "title": t.title,
                "podcast_name": t.podcast_name,
                "host_name": t.host_name,
                "guest_names": t.guest_names or [],
                "cover_url": t.cover_url,
                "content_type": (t.result_composed or {}).get("content_type", ""),
                "core_theme": (t.result_composed or {}).get("core_theme", ""),
                "quote_preview": _quote_preview(t),
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks
        ],
    }


# ─── 素材详情 ───────────────────────────────────────────────

@router.get("/materials/{task_id}")
async def get_material(
    task_id: str,
    x_internal_key: str | None = Header(None),
):
    _check_key(x_internal_key)
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task or task.status != "completed":
        raise HTTPException(status_code=404, detail="素材不存在或未完成")

    composed = task.result_composed or {}
    highlights = task.result_highlights or {}
    preamble = composed.get("preamble", {})

    return {
        "id": task.id,
        "title": task.title,
        "podcast_name": task.podcast_name,
        "host_name": task.host_name,
        "guest_names": task.guest_names or [],
        "cover_url": task.cover_url,
        "content_type": composed.get("content_type", ""),
        "core_theme": composed.get("core_theme", ""),
        "theme_keywords": composed.get("theme_keywords", []),
        "summary_bullets": preamble.get("summary_bullets", []),
        "chapters": [
            {"title": ch.get("title", ""), "summary": ch.get("content", "")[:200], "content": ch.get("content", "")}
            for ch in composed.get("chapters", [])
        ],
        "quotes": highlights.get("quotes", []),
        "editor_preface": task.result_editor_preface or "",
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


# ─── 图片列表 ───────────────────────────────────────────────

@router.get("/materials/{task_id}/images")
async def list_images(
    task_id: str,
    x_internal_key: str | None = Header(None),
):
    """列出可用配图：封面 + PDF 关键页截图。"""
    _check_key(x_internal_key)

    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task or task.status != "completed":
        raise HTTPException(status_code=404, detail="素材不存在或未完成")

    task_dir = Path(settings.STORAGE_DIR) / task_id
    images: list[dict] = []

    # 封面（兼容 cover_rendered.png 和 cover.png）
    for cover_name in ("cover_rendered.png", "cover.png"):
        cover = task_dir / cover_name
        if cover.exists():
            images.append({
                "filename": cover_name,
                "type": "cover",
                "url": f"/api/internal/materials/{task_id}/images/{cover_name}",
                "description": "书籍封面",
            })
            break

    # PDF 关键页截图（由 xhs_exporter 或本接口按需生成）
    xhs_cache_dir = task_dir / "xhs_pages"
    if not xhs_cache_dir.exists() and task.pdf_path and os.path.isfile(task.pdf_path):
        try:
            from single.services.pdf_page_extractor import extract_key_pages
            extract_key_pages(task.pdf_path, str(xhs_cache_dir))
        except Exception as e:
            print(f"[internal] PDF 页面提取失败: {e}")

    if xhs_cache_dir.is_dir():
        desc_map = {
            "page_highlights": "精华提要",
            "page_toc": "目录",
            "page_chapter2": "第二章首页",
        }
        for f in sorted(xhs_cache_dir.iterdir()):
            if f.suffix.lower() in SUPPORTED_IMG_EXT:
                stem = f.stem
                desc_text = desc_map.get(stem, "")
                if not desc_text and stem.startswith("page_body_"):
                    desc_text = f"正文页 {stem.split('_')[-1]}"
                images.append({
                    "filename": f.name,
                    "type": "pdf_page",
                    "url": f"/api/internal/materials/{task_id}/images/{f.name}",
                    "description": desc_text or stem,
                })

    return {"task_id": task_id, "images": images}


# ─── 图片文件代理 ─────────────────────────────────────────

@router.get("/materials/{task_id}/images/{filename}")
async def get_image(
    task_id: str,
    filename: str,
    x_internal_key: str | None = Header(None),
):
    _check_key(x_internal_key)

    task_dir = Path(settings.STORAGE_DIR) / task_id

    # 优先在 xhs_pages/ 子目录查找 PDF 截图
    xhs_path = task_dir / "xhs_pages" / filename
    if xhs_path.is_file():
        return FileResponse(str(xhs_path))

    # 再找任务根目录（封面等）
    root_path = task_dir / filename
    if root_path.is_file():
        return FileResponse(str(root_path))

    raise HTTPException(status_code=404, detail="图片不存在")


# ─── 提交新播客链接 ──────────────────────────────────────

@router.post("/extract")
async def start_extract(
    podcast_url: str,
    cover_style: str = "swiss",
    user_title: str = "",
    user_host_name: str = "",
    user_guest_names: str = "",
    editor_preface: str = "",
    x_internal_key: str | None = Header(None),
):
    """接收播客 URL，创建 Task 并启动完整工作流（不扣费）。

    user_guest_names: 逗号分隔的嘉宾名列表。
    """
    _check_key(x_internal_key)

    from core.services.podcast_service import PodcastService
    from single.background import start_task_in_background

    if not PodcastService.is_supported(podcast_url):
        raise HTTPException(status_code=400, detail="暂只支持小宇宙 FM 或 Apple Podcasts 链接")

    svc = PodcastService()
    try:
        episode = await svc.extract(podcast_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"播客链接解析失败: {e}")

    import re as _re_split
    guest_list = [g.strip() for g in _re_split.split(r'[,、，]+', user_guest_names) if g.strip()] if user_guest_names else []

    task_id = str(uuid4())
    final_host = user_host_name or episode.host_name
    final_guests = guest_list or episode.guest_names

    async with async_session() as session:
        task = Task(
            id=task_id,
            episode_id=episode.episode_id or "",
            episode_title=episode.title,
            title=user_title or episode.title,
            author=final_host or episode.podcast_name,
            description=episode.description or "",
            audio_path=episode.audio_url,
            podcast_url=podcast_url,
            podcast_name=episode.podcast_name,
            host_name=final_host,
            guest_names=final_guests,
            company_names=episode.company_names,
            proper_nouns=episode.proper_nouns,
            name_aliases=episode.name_aliases or None,
            cover_url=episode.cover_url,
            original_cover_url=episode.cover_url,
            cover_style=cover_style,
            user_title=user_title,
            user_host_name=user_host_name,
            user_guest_names=guest_list,
            editor_preface=editor_preface,
            status="pending",
        )
        session.add(task)
        await session.commit()

    start_task_in_background(task_id)
    return {"task_id": task_id, "title": task.title, "status": "pending"}


# ─── 任务状态查询 ────────────────────────────────────────

@router.get("/extract/{task_id}/status")
async def get_extract_status(
    task_id: str,
    x_internal_key: str | None = Header(None),
):
    _check_key(x_internal_key)
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    resp: dict = {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "current_stage": task.current_stage,
    }

    if task.status == "completed":
        composed = task.result_composed or {}
        highlights = task.result_highlights or {}
        preamble = composed.get("preamble", {})
        resp["result"] = {
            "title": task.title,
            "podcast_name": task.podcast_name,
            "host_name": task.host_name,
            "guest_names": task.guest_names or [],
            "core_theme": composed.get("core_theme", ""),
            "theme_keywords": composed.get("theme_keywords", []),
            "summary_bullets": preamble.get("summary_bullets", []),
            "quotes": highlights.get("quotes", []),
        }
    elif task.status == "failed":
        resp["error"] = task.error_message

    return resp
