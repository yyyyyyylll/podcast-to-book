import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from single.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Feedback(Base):
    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), default="")
    category: Mapped[str] = mapped_column(String(20), default="other")
    content: Mapped[str] = mapped_column(Text, default="")
    _image_paths: Mapped[str] = mapped_column("image_paths", Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    admin_note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    @property
    def image_paths(self) -> list[str]:
        try:
            return json.loads(self._image_paths or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    @image_paths.setter
    def image_paths(self, value: list[str]):
        self._image_paths = json.dumps(value)
