from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ticktick_telegram_assistant.db.base import Base


class ReminderEvent(Base):
    __tablename__ = "reminder_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ticktick_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    snoozed_from_event_id: Mapped[int | None] = mapped_column(ForeignKey("reminder_events.id"), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

