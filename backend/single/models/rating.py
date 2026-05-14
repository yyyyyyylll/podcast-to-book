import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from single.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Rating(Base):
    __tablename__ = "ratings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    nps_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    comment: Mapped[str] = mapped_column(Text, default="")
    _image_paths: Mapped[str] = mapped_column("image_paths", Text, default="[]")
    contact_ok: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def image_paths(self) -> list[str]:
        try:
            return json.loads(self._image_paths or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    @image_paths.setter
    def image_paths(self, value: list[str]):
        self._image_paths = json.dumps(value)
