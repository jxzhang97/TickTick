from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ticktick_telegram_assistant.db.base import Base


class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_text: Mapped[str] = mapped_column(Text)
    parsed_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

