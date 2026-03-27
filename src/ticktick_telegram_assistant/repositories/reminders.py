from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent


class ReminderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reminder_event: ReminderEvent) -> ReminderEvent:
        if self._session is not None:
            self._session.add(reminder_event)
        return reminder_event

    def add_many(self, reminder_events: list[ReminderEvent]) -> list[ReminderEvent]:
        if self._session is not None:
            self._session.add_all(reminder_events)
        return reminder_events

    def get_by_dedupe_key(self, dedupe_key: str) -> ReminderEvent | None:
        if self._session is None:
            return None
        return self._session.scalars(
            select(ReminderEvent).where(ReminderEvent.dedupe_key == dedupe_key)
        ).first()

    def list_sent_for_user(self, *, user_id: int) -> list[ReminderEvent]:
        if self._session is None:
            return []
        return list(
            self._session.scalars(
                select(ReminderEvent).where(ReminderEvent.user_id == user_id)
            )
        )

    def get_latest_for_user(
        self,
        *,
        user_id: int,
        event_types: list[str],
        statuses: list[str] | None = None,
    ) -> ReminderEvent | None:
        if self._session is None:
            return None
        statement = select(ReminderEvent).where(
            ReminderEvent.user_id == user_id,
            ReminderEvent.event_type.in_(event_types),
        )
        if statuses:
            statement = statement.where(ReminderEvent.status.in_(statuses))
        statement = statement.order_by(ReminderEvent.created_at.desc(), ReminderEvent.id.desc())
        return self._session.scalars(statement).first()

    def list_due_pending_for_user(self, *, user_id: int, now: datetime) -> list[ReminderEvent]:
        if self._session is None:
            return []
        return list(
            self._session.scalars(
                select(ReminderEvent).where(
                    ReminderEvent.user_id == user_id,
                    ReminderEvent.status == "pending",
                    ReminderEvent.scheduled_at <= now,
                )
            )
        )

    def mark_sent(self, reminder_event: ReminderEvent, *, sent_at: datetime) -> ReminderEvent:
        reminder_event.status = "sent"
        reminder_event.sent_at = sent_at
        if self._session is not None:
            self._session.add(reminder_event)
        return reminder_event
