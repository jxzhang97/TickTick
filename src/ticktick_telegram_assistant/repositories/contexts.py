from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.active_context import ActiveContext


class ContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, context: ActiveContext) -> ActiveContext:
        if self._session is not None:
            self._session.add(context)
        return context

