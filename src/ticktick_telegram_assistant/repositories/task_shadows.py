from sqlalchemy import select
from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow


class TaskShadowRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task_shadow: TaskShadow) -> TaskShadow:
        if self._session is not None:
            self._session.add(task_shadow)
        return task_shadow

    def list_windowed_by_user(self, *, user_id: int) -> list[TaskShadow]:
        return self.list_by_semantic_type(user_id=user_id, semantic_type="windowed")

    def list_memo_by_user(self, *, user_id: int) -> list[TaskShadow]:
        return self.list_by_semantic_type(user_id=user_id, semantic_type="memo")

    def list_by_semantic_type(self, *, user_id: int, semantic_type: str) -> list[TaskShadow]:
        if self._session is None:
            return []
        return list(
            self._session.scalars(
                select(TaskShadow).where(
                    TaskShadow.user_id == user_id,
                    TaskShadow.semantic_type == semantic_type,
                ).order_by(TaskShadow.created_at.desc(), TaskShadow.id.desc())
            )
        )
