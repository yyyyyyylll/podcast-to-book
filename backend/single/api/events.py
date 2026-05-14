from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, Header
from pydantic import BaseModel, ConfigDict, Field
import jwt

from core.config import settings
from single.database import async_session
from single.models.event import Event

router = APIRouter()

KNOWN_EVENTS = {
    "session_started",
    "page_viewed",
    "page_left",
    "user_registered",
    "user_logged_in",
    "user_logged_out",
    "login_started",
    "login_code_sent",
    "login_completed",
    "login_abandoned",
    "landing_cta_clicked",
    "task_form_interacted",
    "cover_selected",
    "cover_previewed",
    "task_submit_attempted",
    "credit_insufficient",
    "task_submitted",
    "task_submit_failed",
    "task_created",
    "credit_consumed",
    "task_stage_changed",
    "task_completed",
    "task_failed",
    "task_cache_hit",
    "task_retried",
    "credit_refunded",
    "speaker_input_shown",
    "speaker_input_submitted",
    "task_processing_viewed",
    "task_processing_completed",
    "result_viewed",
    "pdf_downloaded",
    "epub_downloaded",
    "pdf_print_clicked",
    "unlock_fulltext_clicked",
    "unlock_fulltext_succeeded",
    "unlock_fulltext_failed",
    "unlock_edit_clicked",
    "unlock_edit_succeeded",
    "unlock_edit_failed",
    "edit_entered",
    "lock_upsell_clicked",
    "edit_save_clicked",
    "edit_save_succeeded",
    "edit_save_failed",
    "edit_cancelled",
    "edit_section_used",
    "edit_cover_changed",
    "edit_illustration_action",
    "reader_page_turned",
    "reader_fullscreen_toggled",
    "reader_max_depth",
    "guide_started",
    "guide_step_viewed",
    "guide_skipped",
    "guide_completed",
    "recharge_page_viewed",
    "recharge_pack_selected",
    "recharge_submitted",
    "recharge_success_viewed",
    "payment_created",
    "payment_completed",
    "payment_return_landed",
    "redeem_submitted",
    "redeem_succeeded",
    "redeem_failed",
    "order_list_viewed",
    "order_tab_switched",
    "order_retried",
    "feedback_opened",
    "feedback_submitted",
    "rating_submitted",
    "rating_dismissed",
    "account_changed",
    "account_deleted",
    "referral_page_viewed",
    "referral_copy_clicked",
    "credits_recharge_clicked",
    "credits_load_more",
    "nav_menu_clicked",
    "nav_login_clicked",
}


class EventPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., max_length=100)
    properties: dict = Field(default_factory=dict)
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    uid: Optional[str] = Field(None, alias="_uid")


class EventBatch(BaseModel):
    events: list[EventPayload] = Field(..., max_length=50)


def _extract_user_id(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(
            authorization[7:], settings.JWT_SECRET, algorithms=["HS256"]
        )
        return payload.get("sub")
    except Exception:
        return None


@router.post("/events", status_code=202)
async def collect_events(
    body: EventBatch,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    user_id = _extract_user_id(authorization)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")

    async with async_session() as session:
        for ev in body.events:
            ev_user_id = user_id or ev.uid
            props = {**ev.properties}
            if ev.name not in KNOWN_EVENTS:
                props["_unregistered"] = True

            created_at = None
            if ev.timestamp:
                try:
                    created_at = datetime.fromisoformat(ev.timestamp)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    created_at = None

            event = Event(
                event_name=ev.name,
                user_id=ev_user_id,
                session_id=ev.session_id,
                properties=props,
                page_url=ev.page_url,
                referrer=ev.referrer,
                user_agent=user_agent,
                client_ip=client_ip,
            )
            if created_at:
                event.created_at = created_at
            session.add(event)
        await session.commit()

    return {"ok": True}
