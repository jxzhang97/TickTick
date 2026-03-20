from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow


class TaskShadowRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task_shadow: TaskShadow) -> TaskShadow:
        if self._session is not None:
            self._session.add(task_shadow)
        return task_shadow

