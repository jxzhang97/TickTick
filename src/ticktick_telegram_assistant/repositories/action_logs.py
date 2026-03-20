from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.action_log import ActionLog


class ActionLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, action_log: ActionLog) -> ActionLog:
        if self._session is not None:
            self._session.add(action_log)
        return action_log
