from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ticktick_telegram_assistant.db.base import Base


class TaskShadow(Base):
    __tablename__ = "task_shadows"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ticktick_task_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    semantic_type: Mapped[str] = mapped_column(String(64), index=True)
    normalized_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    list_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tags_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_nl_time: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    timezone_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
