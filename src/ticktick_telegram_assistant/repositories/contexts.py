from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select

from ticktick_telegram_assistant.db.models.active_context import ActiveContext


class ContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, context: ActiveContext) -> ActiveContext:
        existing = self.get_by_user_and_type(user_id=context.user_id, context_type=context.context_type)
        if existing is None:
            if self._session is not None:
                self._session.add(context)
                self._session.flush()
            return context

        existing.payload_json = context.payload_json
        existing.expires_at = context.expires_at
        if self._session is not None:
            self._session.add(existing)
            self._session.flush()
        return existing

    def add(self, context: ActiveContext) -> ActiveContext:
        if self._session is not None:
            self._session.add(context)
            self._session.flush()
        return context

    def get_by_user_and_type(self, *, user_id: int, context_type: str) -> ActiveContext | None:
        if self._session is None:
            return None
        return self._session.scalars(
            select(ActiveContext).where(
                ActiveContext.user_id == user_id,
                ActiveContext.context_type == context_type,
            )
        ).one_or_none()

    def list_by_type(self, *, context_type: str, user_id: int | None = None) -> list[ActiveContext]:
        if self._session is None:
            return []
        statement = select(ActiveContext).where(ActiveContext.context_type == context_type)
        if user_id is not None:
            statement = statement.where(ActiveContext.user_id == user_id)
        return list(self._session.scalars(statement))

    def delete(self, context: ActiveContext) -> None:
        if self._session is not None:
            self._session.delete(context)

    def replace(
        self,
        *,
        user_id: int,
        context_type: str,
        payload_json: dict,
        expires_at: datetime | None = None,
    ) -> ActiveContext:
        context = self.get_by_user_and_type(user_id=user_id, context_type=context_type)
        if context is None:
            context = ActiveContext(
                user_id=user_id,
                context_type=context_type,
                payload_json=payload_json,
                expires_at=expires_at,
            )
            if self._session is not None:
                self._session.add(context)
                self._session.flush()
            return context

        context.payload_json = payload_json
        context.expires_at = expires_at
        if self._session is not None:
            self._session.add(context)
            self._session.flush()
        return context
