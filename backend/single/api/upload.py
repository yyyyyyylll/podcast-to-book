from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from uuid import uuid4
from typing import Optional

from sqlalchemy import func, select

from core.config import settings
from single.database import async_session
from single.models.task import Task
from single.models.user import User
from single.api.deps import get_current_user
from single.services import credit_service
from single.services.analytics_service import track as track_event
from single.services.referral_service import reward_inviter_on_first_book
from single.background import start_task_in_background
from single.utils import compute_content_hash

router = APIRouter()


@router.get("/cover-styles")
async def get_cover_styles():
    """获取所有可用的封面风格"""
    from core.services.cover_service import get_available_styles
    return get_available_styles()

@router.post("/upload-url")
async def upload_podcast_url(
    request: Request,
    podcast_url: str = Form(...),
    title: str = Form(default=""),
    host_name: str = Form(default=""),
    guest_names: str = Form(default=""),
    editor_preface: str = Form(default=""),
    cover_style: str = Form(default="classic"),
    current_user: User = Depends(get_current_user),
):
    """通过播客链接启动处理任务

    所有信息字段均为选填，AI 会自动从播客链接中识别。
    """
    from core.services.podcast_service import PodcastService

    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    user_agent = request.headers.get("user-agent", "")[:512]

    if not PodcastService.is_supported(podcast_url):
        raise HTTPException(status_code=400, detail="暂只支持小宇宙 FM 或 Apple Podcasts 链接")

    # 判断是否首本书（用户无已完成的 task）
    async with async_session() as session:
        has_completed = await session.execute(
            select(Task.id).where(
                Task.user_id == current_user.id,
                Task.status == "completed",
            ).limit(1)
        )
        is_first_book = has_completed.scalar_one_or_none() is None

    cost = settings.CREDIT_COST_FIRST_BOOK_GENERATE if is_first_book else settings.CREDIT_COST_BOOK_GENERATE
    initial_unlock = "preview" if is_first_book else "full_text"

    async with async_session() as session:
        has_balance = await credit_service.check_balance(session, current_user.id, cost)
        if not has_balance:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足，生成电子书需要 {cost} 积分，请先充值",
            )
        await credit_service.deduct(
            session, current_user.id, cost,
            ref_type="task", ref_id="pending",
            description=f"生成电子书（{'首本体验' if is_first_book else '标准'}）",
        )
        await track_event(
            "credit_consumed", session,
            user_id=current_user.id,
            properties={"task_id": "pending", "amount": cost, "balance_after": None, "is_first_book": is_first_book},
            client_ip=client_ip, user_agent=user_agent,
        )
        await session.commit()

    svc = PodcastService()
    try:
        episode = await svc.extract(podcast_url)
    except Exception as e:
        async with async_session() as session:
            await credit_service.refund(
                session, current_user.id, cost,
                ref_type="task", ref_id="parse_failed",
                description="播客解析失败，退回积分",
            )
            await session.commit()
        await _save_failed_task(
            podcast_url=podcast_url, title=title, editor_preface=editor_preface,
            client_ip=client_ip, user_agent=user_agent,
            error=f"播客链接解析失败: {e}",
        )
        raise HTTPException(status_code=400, detail=f"播客链接解析失败: {e}")

    MAX_AUDIO_DURATION = 5 * 3600
    if episode.duration and episode.duration > MAX_AUDIO_DURATION:
        hours = episode.duration / 3600
        async with async_session() as session:
            await credit_service.refund(
                session, current_user.id, cost,
                ref_type="task", ref_id="duration_exceeded",
                description="音频时长超限，退回积分",
            )
            await session.commit()
        raise HTTPException(
            status_code=400,
            detail=f"该播客时长约 {hours:.1f} 小时，目前仅支持 5 小时以内的音频。我们正在优化对超长播客的支持，敬请期待！",
        )

    task_id = str(uuid4())
    final_title = title or episode.title
    final_host = host_name or episode.host_name
    final_author = final_host or episode.podcast_name

    import re as _re_split
    user_guests = [g.strip() for g in _re_split.split(r'[,、，]+', guest_names) if g.strip()] if guest_names else []
    final_guests = user_guests or episode.guest_names

    content_hash = compute_content_hash(
        episode_id=episode.episode_id or "",
        user_title=title,
        user_host_name=host_name.strip(),
        user_guest_names=user_guests,
        editor_preface=editor_preface,
    )

    async with async_session() as session:
        task = Task(
            id=task_id,
            episode_id=episode.episode_id or "",
            episode_title=episode.title,
            title=final_title,
            user_title=title,
            user_host_name=host_name.strip(),
            user_guest_names=user_guests,
            author=final_author,
            description=episode.description or "",
            editor_preface=editor_preface,
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
            credit_charged=cost,
            unlock_level=initial_unlock,
            content_hash=content_hash,
            client_ip=client_ip,
            user_agent=user_agent,
            user_id=current_user.id,
            status="pending",
        )
        session.add(task)
        await session.flush()
        task_count_result = await session.execute(
            select(func.count(Task.id)).where(Task.user_id == current_user.id)
        )
        nth_task = task_count_result.scalar() or 0

        await track_event(
            "task_created", session,
            user_id=current_user.id,
            properties={
                "task_id": task_id,
                "podcast_url_domain": urlparse(podcast_url).netloc,
                "cover_style": cover_style,
                "credit_cost": cost,
                "nth_task": nth_task,
                "has_user_title": bool(title),
                "has_guest": bool(guest_names),
                "has_host": bool(host_name),
                "has_preface": bool(editor_preface),
            },
            client_ip=client_ip, user_agent=user_agent,
        )
        await session.commit()

    # 被邀请用户首次生成书 → 给邀请人发积分
    if is_first_book and current_user.invited_by:
        try:
            async with async_session() as session:
                user_result = await session.execute(
                    select(User).where(User.id == current_user.id)
                )
                fresh_user = user_result.scalar_one_or_none()
                if fresh_user:
                    await reward_inviter_on_first_book(session, fresh_user)
                    await session.commit()
        except Exception:
            pass  # 奖励失败不影响主流程

    start_task_in_background(task_id)

    return {
        "task_id": task_id,
        "title": final_title,
        "author": final_author,
        "status": "pending",
        "message": "播客解析完成，任务已创建",
    }


async def _save_failed_task(*, podcast_url: str, title: str, editor_preface: str,
                            client_ip: str, user_agent: str, error: str):
    """将解析阶段就失败的请求也记录到数据库，方便管理后台追踪。"""
    try:
        async with async_session() as session:
            task = Task(
                id=str(uuid4()),
                title=title or "(解析失败)",
                author="",
                description="",
                editor_preface=editor_preface,
                audio_path="",
                podcast_url=podcast_url,
                client_ip=client_ip,
                user_agent=user_agent,
                status="failed",
                current_stage="upload",
                error_message=error,
            )
            session.add(task)
            await session.commit()
    except Exception:
        pass
