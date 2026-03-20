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
    ) -> MemoryFact:
        fact = MemoryFact(
            user_id=user_id,
            key=key,
            value_json=value_json,
            memory_type=memory_type.value,
        )
        if self._session is not None:
            self._session.add(fact)
        return fact

