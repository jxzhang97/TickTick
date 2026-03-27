from datetime import datetime
from zoneinfo import ZoneInfo

from ticktick_telegram_assistant.domain.schemas import ConversationContext


class ContextBuilder:
    def build(
        self,
        text: str,
        *,
        current_timezone: str = "America/Los_Angeles",
        now: datetime | None = None,
        memory_items: list[str] | None = None,
        candidate_tasks: list[str] | None = None,
    ) -> ConversationContext:
        current_dt = now.astimezone(ZoneInfo(current_timezone)) if now is not None else datetime.now(
            ZoneInfo(current_timezone)
        )
        return ConversationContext(
            user_text=text,
            current_timezone=current_timezone,
            current_local_time=current_dt.isoformat(),
            memory_items=memory_items or [],
            candidate_tasks=candidate_tasks or [],
        )

    def split_lines(self, text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip()]
