import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from single.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    user_title: Mapped[str] = mapped_column(Text, default="")
    user_host_name: Mapped[str] = mapped_column(String(255), default="")
    user_guest_names: Mapped[dict] = mapped_column(JSON, default=list)
    editor_preface: Mapped[str] = mapped_column(Text, default="")

    audio_path: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_path: Mapped[str] = mapped_column(Text, nullable=True)

    # 播客元数据
    episode_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    episode_title: Mapped[str] = mapped_column(String(255), default="")
    podcast_url: Mapped[str] = mapped_column(Text, default="")
    podcast_name: Mapped[str] = mapped_column(String(255), default="")
    host_name: Mapped[str] = mapped_column(String(255), default="")
    guest_names: Mapped[dict] = mapped_column(JSON, default=list)
    company_names: Mapped[dict] = mapped_column(JSON, default=list)
    proper_nouns: Mapped[dict] = mapped_column(JSON, default=list)
    name_aliases: Mapped[dict] = mapped_column(JSON, nullable=True)
    cover_url: Mapped[str] = mapped_column(Text, default="")
    original_cover_url: Mapped[str] = mapped_column(Text, default="")
    cover_style: Mapped[str] = mapped_column(String(50), default="classic")
    custom_full_cover_url: Mapped[str] = mapped_column(Text, default="")
    custom_back_cover_url: Mapped[str] = mapped_column(Text, default="")

    # 缓存复用
    content_hash: Mapped[str] = mapped_column(String(32), nullable=True, index=True)
    cached_from: Mapped[str] = mapped_column(String(36), nullable=True)

    # 所属用户（可选，向后兼容）
    user_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    credit_charged: Mapped[int] = mapped_column(Integer, nullable=True)

    # 提交者信息
    client_ip: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")

    # 状态
    status: Mapped[str] = mapped_column(String(50), default="pending")
    current_stage: Mapped[str] = mapped_column(String(50), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)

    # 各阶段结果 (JSONB)
    result_transcription: Mapped[dict] = mapped_column(JSON, nullable=True)
    result_composed: Mapped[dict] = mapped_column(JSON, nullable=True)
    result_highlights: Mapped[dict] = mapped_column(JSON, nullable=True)
    result_annotated: Mapped[dict] = mapped_column(JSON, nullable=True)
    result_illustrations: Mapped[dict] = mapped_column(JSON, nullable=True)
    result_editor_preface: Mapped[str] = mapped_column(Text, nullable=True)
    typst_source: Mapped[str] = mapped_column(Text, nullable=True)

    # 解锁状态：开源版默认 full_access（开源版无积分/付费，全部功能开放）
    unlock_level: Mapped[str] = mapped_column(String(20), default="full_access")

    # 编辑次数
    edit_count: Mapped[int] = mapped_column(Integer, default=0)

    # Token 用量统计
    usage_stats: Mapped[dict] = mapped_column(JSON, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
