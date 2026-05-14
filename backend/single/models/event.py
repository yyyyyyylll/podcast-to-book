import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from single.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    page_url: Mapped[str] = mapped_column(String(2048), nullable=True)
    referrer: Mapped[str] = mapped_column(String(2048), nullable=True)
    user_agent: Mapped[str] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
