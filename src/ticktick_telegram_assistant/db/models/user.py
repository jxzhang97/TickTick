from datetime import datetime

from sqlalchemy import DateTime, Text, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ticktick_telegram_assistant.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    tone_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    primary_timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    current_timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    timezone_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ticktick_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticktick_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticktick_token_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ticktick_token_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticktick_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ticktick_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
