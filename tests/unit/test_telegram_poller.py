import pytest

from ticktick_telegram_assistant.domain.schemas import TelegramReply
from ticktick_telegram_assistant.services.conversation_service import TelegramUpdate


class FakeTelegramClient:
    def __init__(self, updates: list[dict]) -> None:
        self._updates = updates
        self.calls: list[dict] = []
        self.sent_messages: list[dict] = []

    async def get_updates(self, *, offset: int | None, timeout: int) -> list[dict]:
        self.calls.append({"offset": offset, "timeout": timeout})
        return self._updates

    async def send_message(self, *, chat_id: int, text: str) -> dict:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return {"ok": True}


class FakeConversationService:
    def __init__(self) -> None:
        self.handled: list[TelegramUpdate] = []
        self.fail_update_ids: set[int] = set()

    async def handle_update(self, update: TelegramUpdate) -> list[TelegramReply]:
        self.handled.append(update)
        if update.update_id in self.fail_update_ids:
            raise RuntimeError("boom")
        return [TelegramReply(chat_id=update.message.chat.id, text="收到，我记下了。")]


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
    assert client.sent_messages == [{"chat_id": 99, "text": "收到，我记下了。"}]
    assert next_offset == 102


@pytest.mark.asyncio
async def test_poll_once_advances_offset_even_when_one_update_handler_crashes() -> None:
    from ticktick_telegram_assistant.integrations.telegram_poller import TelegramPoller

    client = FakeTelegramClient(
        updates=[
            {
                "update_id": 101,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 99, "type": "private"},
                    "text": "第一条",
                },
            },
            {
                "update_id": 102,
                "message": {
                    "message_id": 2,
                    "chat": {"id": 99, "type": "private"},
                    "text": "第二条",
                },
            },
        ]
    )
    service = FakeConversationService()
    service.fail_update_ids.add(101)
    poller = TelegramPoller(telegram_client=client, conversation_service=service, timeout_seconds=30)

    next_offset = await poller.poll_once(offset=100)

    assert [update.update_id for update in service.handled] == [101, 102]
    assert client.sent_messages == [{"chat_id": 99, "text": "收到，我记下了。"}]
    assert next_offset == 103
