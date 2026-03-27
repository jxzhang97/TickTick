import pytest

from ticktick_telegram_assistant.services.conversation_service import TelegramUpdate


class FakeTelegramClient:
    def __init__(self, updates: list[dict]) -> None:
        self._updates = updates
        self.calls: list[dict] = []

    async def get_updates(self, *, offset: int | None, timeout: int) -> list[dict]:
        self.calls.append({"offset": offset, "timeout": timeout})
        return self._updates


class FakeConversationService:
    def __init__(self) -> None:
        self.handled: list[TelegramUpdate] = []

    async def handle_update(self, update: TelegramUpdate) -> None:
        self.handled.append(update)


@pytest.mark.asyncio
async def test_poll_once_processes_updates_and_returns_next_offset() -> None:
    from ticktick_telegram_assistant.integrations.telegram_poller import TelegramPoller

    client = FakeTelegramClient(
        updates=[
            {
                "update_id": 101,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 99, "type": "private"},
                    "text": "明天提醒我交作业",
                },
            }
        ]
    )
    service = FakeConversationService()
    poller = TelegramPoller(telegram_client=client, conversation_service=service, timeout_seconds=30)

    next_offset = await poller.poll_once(offset=100)

    assert client.calls == [{"offset": 100, "timeout": 30}]
    assert [update.update_id for update in service.handled] == [101]
    assert next_offset == 102
