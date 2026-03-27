from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.active_context import ActiveContext


class ContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, context: ActiveContext) -> ActiveContext:
        if self._session is not None:
            self._session.add(context)
        return context

    def list_by_type(self, *, context_type: str) -> list[ActiveContext]:
        if self._session is None:
            return []
        return (
            self._session.query(ActiveContext)
            .filter(ActiveContext.context_type == context_type)
            .all()
        )

    def delete(self, context: ActiveContext) -> None:
        if self._session is not None:
            self._session.delete(context)
