import os
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, func, desc

from core.config import settings
from single.database import async_session
from single.models.feedback import Feedback
from single.models.user import User
from single.api.deps import get_current_user

router = APIRouter()

CATEGORY_LABELS = {
    "issue": "问题反馈",
    "suggestion": "功能建议",
    "other": "其他",
}

MAX_IMAGES = 6
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


# ─── Webhook notifications ────────────────────────────────

async def _notify_webhook(fb: Feedback, user: User):
    category_label = CATEGORY_LABELS.get(fb.category, fb.category)
    snippet = fb.content[:200] + ("…" if len(fb.content) > 200 else "")
    task_line = f"\n关联任务：{fb.task_id}" if fb.task_id else ""

    async with httpx.AsyncClient(timeout=10) as client:
        if settings.FEISHU_WEBHOOK_URL:
            try:
                await client.post(settings.FEISHU_WEBHOOK_URL, json={
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {"tag": "plain_text", "content": f"📬 新用户反馈 · {category_label}"},
                            "template": "blue",
                        },
                        "elements": [
                            {"tag": "div", "text": {"tag": "lark_md", "content": snippet}},
                            {"tag": "div", "text": {
                                "tag": "lark_md",
                                "content": f"用户：{user.nickname}（{user.phone}）{task_line}",
                            }},
                        ],
                    },
                })
            except Exception as e:
                print(f"[feedback] 飞书推送失败: {e}")

        if settings.WECOM_WEBHOOK_URL:
            try:
                await client.post(settings.WECOM_WEBHOOK_URL, json={
                    "msgtype": "markdown",
                    "markdown": {
                        "content": (
                            f"**📬 新用户反馈 · {category_label}**\n"
                            f"{snippet}\n"
                            f"> 用户：{user.nickname}（{user.phone}）{task_line}"
                        ),
                    },
                })
            except Exception as e:
                print(f"[feedback] 企微推送失败: {e}")


# ─── User API ─────────────────────────────────────────────

@router.post("/feedback")
async def submit_feedback(
    category: str = Form("other"),
    content: str = Form(..., max_length=2000),
    task_id: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
):
    if category not in CATEGORY_LABELS:
        raise HTTPException(status_code=400, detail="无效的反馈分类")
    if not content.strip():
        raise HTTPException(status_code=400, detail="反馈内容不能为空")
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"最多上传 {MAX_IMAGES} 张截图")

    fb_id = str(uuid.uuid4())
    saved_paths: list[str] = []

    if images:
        fb_dir = os.path.join(settings.STORAGE_DIR, "feedback", fb_id)
        os.makedirs(fb_dir, exist_ok=True)
        for i, img in enumerate(images):
            if img.size and img.size > MAX_IMAGE_SIZE:
                raise HTTPException(status_code=400, detail=f"图片 {img.filename} 超过 5MB 限制")
            ext = os.path.splitext(img.filename or "img.jpg")[1] or ".jpg"
            filename = f"{i}{ext}"
            filepath = os.path.join(fb_dir, filename)
            data = await img.read()
            if len(data) > MAX_IMAGE_SIZE:
                raise HTTPException(status_code=400, detail=f"图片 {img.filename} 超过 5MB 限制")
            with open(filepath, "wb") as f:
                f.write(data)
            saved_paths.append(f"feedback/{fb_id}/{filename}")

    fb = Feedback(
        id=fb_id,
        user_id=user.id,
        task_id=task_id,
        category=category,
        content=content.strip(),
    )
    fb.image_paths = saved_paths

    async with async_session() as session:
        session.add(fb)
        await session.commit()

    try:
        await _notify_webhook(fb, user)
    except Exception as e:
        print(f"[feedback] Webhook 通知异常: {e}")

    return {"id": fb_id, "message": "反馈已提交，感谢您的意见！"}


# ─── Admin API ────────────────────────────────────────────

def _check_admin(token: str | None):
    secret = settings.ADMIN_SECRET
    if secret and token != secret:
        raise HTTPException(status_code=401, detail="管理员密码错误")


@router.get("/admin/feedbacks")
async def list_feedbacks(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    x_admin_token: str | None = Header(None),
):
    _check_admin(x_admin_token)
    async with async_session() as session:
        base_q = select(Feedback)
        count_q = select(func.count(Feedback.id))
        if status:
            base_q = base_q.where(Feedback.status == status)
            count_q = count_q.where(Feedback.status == status)

        total = (await session.execute(count_q)).scalar() or 0

        rows = (await session.execute(
            base_q.order_by(desc(Feedback.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).scalars().all()

        user_ids = list({r.user_id for r in rows})
        users_map: dict[str, User] = {}
        if user_ids:
            users = (await session.execute(
                select(User).where(User.id.in_(user_ids))
            )).scalars().all()
            users_map = {u.id: u for u in users}

    pending_count_result = 0
    async with async_session() as session:
        pending_count_result = (await session.execute(
            select(func.count(Feedback.id)).where(Feedback.status == "pending")
        )).scalar() or 0

    items = []
    for fb in rows:
        u = users_map.get(fb.user_id)
        items.append({
            "id": fb.id,
            "user_id": fb.user_id,
            "user_phone": u.phone if u else "",
            "user_nickname": u.nickname if u else "",
            "task_id": fb.task_id,
            "category": fb.category,
            "category_label": CATEGORY_LABELS.get(fb.category, fb.category),
            "content": fb.content,
            "image_paths": fb.image_paths,
            "status": fb.status,
            "admin_note": fb.admin_note,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        })

    return {
        "feedbacks": items,
        "total": total,
        "pending_count": pending_count_result,
        "page": page,
        "page_size": page_size,
    }


class UpdateFeedbackStatus(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None


@router.put("/admin/feedbacks/{feedback_id}/status")
async def update_feedback_status(
    feedback_id: str,
    body: UpdateFeedbackStatus,
    x_admin_token: str | None = Header(None),
):
    _check_admin(x_admin_token)
    if body.status and body.status not in ("pending", "read", "resolved"):
        raise HTTPException(status_code=400, detail="无效的状态值")

    async with async_session() as session:
        fb = (await session.execute(
            select(Feedback).where(Feedback.id == feedback_id)
        )).scalar_one_or_none()
        if not fb:
            raise HTTPException(status_code=404, detail="反馈不存在")

        if body.status:
            fb.status = body.status
        if body.admin_note is not None:
            fb.admin_note = body.admin_note
        await session.commit()

    return {"ok": True}
