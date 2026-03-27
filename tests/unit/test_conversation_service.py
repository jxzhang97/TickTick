import pytest

from ticktick_telegram_assistant.domain.schemas import PlannedConversation
from ticktick_telegram_assistant.services.conversation_service import (
    ConversationService,
    TelegramUpdate,
)


class FakePlanner:
    def __init__(self, planned: PlannedConversation) -> None:
        self._planned = planned
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        return self._planned


@pytest.mark.asyncio
async def test_handle_update_returns_planner_reply() -> None:
    planner = FakePlanner(PlannedConversation(assistant_reply="今晚我会陪你盯着这件事。"))
    service = ConversationService(planner=planner)
    update = TelegramUpdate.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 7,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "下周提醒我交报告",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [(reply.chat_id, reply.text) for reply in replies] == [
        (99, "今晚我会陪你盯着这件事。"),
    ]
    assert planner.contexts[0].user_text == "下周提醒我交报告"


@pytest.mark.asyncio
async def test_handle_update_falls_back_with_ticktick_setup_reply() -> None:
    planner = FakePlanner(PlannedConversation())
    service = ConversationService(planner=planner)
    update = TelegramUpdate.model_validate(
        {
            "update_id": 2,
            "message": {
                "message_id": 8,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "今天有什么安排",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert replies[0].chat_id == 99
    assert "TickTick" in replies[0].text
    assert "今天" in replies[0].text
