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
