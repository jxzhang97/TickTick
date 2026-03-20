from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.repositories.memory import MemoryRepository


def test_memory_repository_exposes_upsert() -> None:
    repo = MemoryRepository(session=None)
    assert callable(repo.upsert_fact)
    assert MemoryType.PREFERENCE.value == "preference"
