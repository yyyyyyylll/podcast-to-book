import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from sqlalchemy import select, func, desc

from core.config import settings
from single.database import async_session
from single.models.rating import Rating
from single.models.task import Task
from single.models.user import User
from single.api.deps import get_current_user

router = APIRouter()

SCORE_LABELS = {
    1: "\u5f88\u5dee",
    2: "\u8f83\u5dee",
    3: "\u4e00\u822c",
    4: "\u6ee1\u610f",
    5: "\u975e\u5e38\u6ee1\u610f",
}

MAX_IMAGES = 6
MAX_IMAGE_SIZE = 5 * 1024 * 1024


async def _notify_low_score(rating: Rating, user: User, task_title: str):
    score_label = SCORE_LABELS.get(rating.score, str(rating.score))
    if rating.comment:
        snippet = rating.comment[:200] + ("\u2026" if len(rating.comment) > 200 else "")
    else:
        snippet = "\uff08\u65e0\u6587\u5b57\u8bc4\u4ef7\uff09"

    feishu_url = getattr(settings, "FEISHU_WEBHOOK_URL", "")
    wecom_url = getattr(settings, "WECOM_WEBHOOK_URL", "")
    if not feishu_url and not wecom_url:
        return

    async with httpx.AsyncClient(timeout=10) as client:
        if feishu_url:
            try:
                await client.post(feishu_url, json={
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {
                                "tag": "plain_text",
                                "content": f"\u26a0\ufe0f \u4f4e\u5206\u8bc4\u4ef7 \u00b7 {rating.score}\u5206\uff08{score_label}\uff09",
                            },
                            "template": "red",
                        },
                        "elements": [
                            {"tag": "div", "text": {
                                "tag": "lark_md",
                                "content": f"**\u4e66\u540d**\uff1a{task_title}\n**\u8bc4\u4ef7**\uff1a{snippet}",
                            }},
                            {"tag": "div", "text": {
                                "tag": "lark_md",
                                "content": f"\u7528\u6237\uff1a{user.nickname}\uff08{user.phone}\uff09\n\u4efb\u52a1\uff1a{rating.task_id}",
                            }},
                        ],
                    },
                })
            except Exception as e:
                print(f"[rating] feishu push failed: {e}")

        if wecom_url:
            try:
                await client.post(wecom_url, json={
                    "msgtype": "markdown",
                    "markdown": {
                        "content": (
                            f"**\u26a0\ufe0f \u4f4e\u5206\u8bc4\u4ef7 \u00b7 {rating.score}\u5206\uff08{score_label}\uff09**\n"
                            f"\u4e66\u540d\uff1a{task_title}\n"
                            f"{snippet}\n"
                            f"> \u7528\u6237\uff1a{user.nickname}\uff08{user.phone}\uff09\n> \u4efb\u52a1\uff1a{rating.task_id}"
                        ),
                    },
                })
            except Exception as e:
                print(f"[rating] wecom push failed: {e}")


@router.post("/ratings")
async def submit_rating(
    task_id: str = Form(...),
    score: int = Form(..., ge=1, le=5),
    nps_score: int = Form(0, ge=0, le=5),
    comment: str = Form(""),
    contact_ok: bool = Form(False),
    images: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
):
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"\u6700\u591a\u4e0a\u4f20 {MAX_IMAGES} \u5f20\u622a\u56fe")

    async with async_session() as session:
        task = (await session.execute(
            select(Task).where(Task.id == task_id)
        )).scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="\u4efb\u52a1\u4e0d\u5b58\u5728")
        if task.user_id != user.id:
            raise HTTPException(status_code=403, detail="\u65e0\u6743\u8bc4\u4ef7\u6b64\u4efb\u52a1")

        existing = (await session.execute(
            select(Rating).where(Rating.task_id == task_id)
        )).scalar_one_or_none()

        saved_paths: list[str] = []
        rating_id = existing.id if existing else str(uuid.uuid4())

        if images:
            rt_dir = os.path.join(settings.STORAGE_DIR, "ratings", rating_id)
            os.makedirs(rt_dir, exist_ok=True)
            for i, img in enumerate(images):
                if img.size and img.size > MAX_IMAGE_SIZE:
                    raise HTTPException(status_code=400, detail=f"\u56fe\u7247 {img.filename} \u8d85\u8fc7 5MB \u9650\u5236")
                ext = os.path.splitext(img.filename or "img.jpg")[1] or ".jpg"
                filename = f"{i}{ext}"
                filepath = os.path.join(rt_dir, filename)
                data = await img.read()
                if len(data) > MAX_IMAGE_SIZE:
                    raise HTTPException(status_code=400, detail=f"\u56fe\u7247 {img.filename} \u8d85\u8fc7 5MB \u9650\u5236")
                with open(filepath, "wb") as f:
                    f.write(data)
                saved_paths.append(f"ratings/{rating_id}/{filename}")

        if existing:
            existing.score = score
            existing.nps_score = nps_score
            existing.comment = comment.strip()
            existing.contact_ok = contact_ok
            if saved_paths:
                existing.image_paths = saved_paths
            existing.updated_at = datetime.now(timezone.utc)
            await session.commit()
            rt = existing
        else:
            rt = Rating(
                id=rating_id,
                task_id=task_id,
                user_id=user.id,
                score=score,
                nps_score=nps_score,
                comment=comment.strip(),
                contact_ok=contact_ok,
            )
            if saved_paths:
                rt.image_paths = saved_paths
            session.add(rt)
            await session.commit()

    if score <= 2:
        try:
            await _notify_low_score(rt, user, task.title)
        except Exception as e:
            print(f"[rating] Webhook error: {e}")

    return {"id": rt.id, "message": "\u8bc4\u5206\u5df2\u63d0\u4ea4\uff0c\u611f\u8c22\u60a8\u7684\u53cd\u9988\uff01"}


@router.get("/ratings/{task_id}")
async def get_my_rating(
    task_id: str,
    user: User = Depends(get_current_user),
):
    async with async_session() as session:
        rt = (await session.execute(
            select(Rating).where(Rating.task_id == task_id, Rating.user_id == user.id)
        )).scalar_one_or_none()

    if not rt:
        return None

    return {
        "id": rt.id,
        "task_id": rt.task_id,
        "score": rt.score,
        "nps_score": rt.nps_score,
        "comment": rt.comment,
        "image_paths": rt.image_paths,
        "contact_ok": rt.contact_ok,
        "created_at": rt.created_at.isoformat() if rt.created_at else None,
        "updated_at": rt.updated_at.isoformat() if rt.updated_at else None,
    }


def _check_admin(token: str | None):
    secret = settings.ADMIN_SECRET
    if secret and token != secret:
        raise HTTPException(status_code=401, detail="\u7ba1\u7406\u5458\u5bc6\u7801\u9519\u8bef")


@router.get("/admin/ratings")
async def list_ratings(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    score: Optional[int] = Query(None, ge=1, le=5),
    x_admin_token: str | None = Header(None),
):
    _check_admin(x_admin_token)
    async with async_session() as session:
        base_q = select(Rating)
        count_q = select(func.count(Rating.id))
        if score is not None:
            base_q = base_q.where(Rating.score == score)
            count_q = count_q.where(Rating.score == score)

        total = (await session.execute(count_q)).scalar() or 0

        rows = (await session.execute(
            base_q.order_by(desc(Rating.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).scalars().all()

        user_ids = list({r.user_id for r in rows})
        task_ids = list({r.task_id for r in rows})

        users_map: dict[str, User] = {}
        if user_ids:
            users = (await session.execute(
                select(User).where(User.id.in_(user_ids))
            )).scalars().all()
            users_map = {u.id: u for u in users}

        tasks_map: dict[str, Task] = {}
        if task_ids:
            tasks = (await session.execute(
                select(Task).where(Task.id.in_(task_ids))
            )).scalars().all()
            tasks_map = {t.id: t for t in tasks}

    items = []
    for rt in rows:
        u = users_map.get(rt.user_id)
        t = tasks_map.get(rt.task_id)
        items.append({
            "id": rt.id,
            "task_id": rt.task_id,
            "task_title": t.title if t else "",
            "user_id": rt.user_id,
            "user_phone": u.phone if u else "",
            "user_nickname": u.nickname if u else "",
            "score": rt.score,
            "nps_score": rt.nps_score,
            "comment": rt.comment,
            "image_paths": rt.image_paths,
            "contact_ok": rt.contact_ok,
            "created_at": rt.created_at.isoformat() if rt.created_at else None,
            "updated_at": rt.updated_at.isoformat() if rt.updated_at else None,
        })

    return {
        "ratings": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/admin/ratings/stats")
async def rating_stats(
    x_admin_token: str | None = Header(None),
):
    _check_admin(x_admin_token)
    async with async_session() as session:
        total = (await session.execute(select(func.count(Rating.id)))).scalar() or 0
        avg_score = (await session.execute(select(func.avg(Rating.score)))).scalar()
        avg_nps = (await session.execute(
            select(func.avg(Rating.nps_score)).where(Rating.nps_score > 0)
        )).scalar()
        contact_ok_count = (await session.execute(
            select(func.count(Rating.id)).where(Rating.contact_ok == True)
        )).scalar() or 0

        dist_rows = (await session.execute(
            select(Rating.score, func.count(Rating.id))
            .group_by(Rating.score)
            .order_by(Rating.score)
        )).all()

    distribution = {i: 0 for i in range(1, 6)}
    for score_val, cnt in dist_rows:
        distribution[score_val] = cnt

    return {
        "total": total,
        "avg_score": round(float(avg_score), 2) if avg_score is not None else 0,
        "avg_nps": round(float(avg_nps), 2) if avg_nps is not None else 0,
        "contact_ok_count": contact_ok_count,
        "distribution": distribution,
    }
