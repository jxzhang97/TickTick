from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.memory_fact import MemoryFact
from ticktick_telegram_assistant.domain.enums import MemoryType


class MemoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_fact(
        self,
        *,
        user_id: int,
        key: str,
        value_json: dict,
        memory_type: MemoryType,
        confidence: float = 1.0,
        source_type: str | None = None,
        last_confirmed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryFact:
        fact = self.get_fact(user_id=user_id, key=key, memory_type=memory_type)
        if fact is None:
            fact = MemoryFact(
                user_id=user_id,
                key=key,
                value_json=value_json,
                memory_type=memory_type.value,
            )
        else:
            fact.value_json = value_json
        fact.confidence = confidence
        fact.source_type = source_type
        fact.last_confirmed_at = last_confirmed_at
        fact.expires_at = expires_at
        if self._session is not None:
            self._session.add(fact)
            self._session.flush()
        return fact

    def get_fact(self, *, user_id: int, key: str, memory_type: MemoryType) -> MemoryFact | None:
        if self._session is None:
            return None
        return (
            self._session.scalars(
                select(MemoryFact).where(
                    MemoryFact.user_id == user_id,
                    MemoryFact.key == key,
                    MemoryFact.memory_type == memory_type.value,
                )
            ).one_or_none()
        )

    def list_facts(
        self,
        *,
        user_id: int,
        memory_types: list[MemoryType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryFact]:
        if self._session is None:
            return []
        statement = select(MemoryFact).where(MemoryFact.user_id == user_id)
        if memory_types:
            statement = statement.where(MemoryFact.memory_type.in_([item.value for item in memory_types]))
        statement = statement.order_by(MemoryFact.created_at.desc(), MemoryFact.id.desc())
        if limit is not None:
            statement = statement.limit(limit)
        return list(self._session.scalars(statement))

    def delete_fact(self, fact: MemoryFact) -> None:
        if self._session is not None:
            self._session.delete(fact)
