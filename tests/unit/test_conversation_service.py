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


class FakeTickTickOAuthService:
    def __init__(self, *, connected: bool, auth_url: str | None) -> None:
        self._connected = connected
        self._auth_url = auth_url
        self.calls: list[dict] = []

    async def has_connection(self, *, telegram_user_id: str) -> bool:
        self.calls.append({"method": "has_connection", "telegram_user_id": telegram_user_id})
        return self._connected

    async def create_authorization_url(self, *, telegram_user_id: str, display_name: str | None) -> str | None:
        self.calls.append(
            {
                "method": "create_authorization_url",
                "telegram_user_id": telegram_user_id,
                "display_name": display_name,
            }
        )
        return self._auth_url


class FakeTodayBriefService:
    def __init__(self, brief: str) -> None:
        self._brief = brief
        self.calls: list[dict] = []

    async def build_today_brief(self, *, telegram_user_id: str, now=None) -> str:
        self.calls.append({"telegram_user_id": telegram_user_id, "now": now})
        return self._brief


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


@pytest.mark.asyncio
async def test_handle_update_returns_oauth_link_for_ticktick_request_when_disconnected() -> None:
    planner = FakePlanner(PlannedConversation())
    oauth_service = FakeTickTickOAuthService(
        connected=False,
        auth_url="https://ticktick.com/oauth/authorize?state=abc",
    )
    service = ConversationService(planner=planner, ticktick_oauth_service=oauth_service)
    update = TelegramUpdate.model_validate(
        {
            "update_id": 3,
            "message": {
                "message_id": 9,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "今天有什么安排",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "授权" in replies[0].text
    assert "https://ticktick.com/oauth/authorize?state=abc" in replies[0].text
    assert planner.contexts == []
    assert oauth_service.calls == [
        {"method": "has_connection", "telegram_user_id": "99"},
        {
            "method": "create_authorization_url",
            "telegram_user_id": "99",
            "display_name": None,
        },
    ]


@pytest.mark.asyncio
async def test_handle_update_returns_today_brief_when_ticktick_connected() -> None:
    planner = FakePlanner(PlannedConversation())
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    today_brief_service = FakeTodayBriefService("今天我先帮你抓重点：\n- 周五 11:00 发邮件")
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        today_brief_service=today_brief_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 4,
            "message": {
                "message_id": 10,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "今天有什么安排",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["今天我先帮你抓重点：\n- 周五 11:00 发邮件"]
    assert planner.contexts == []
    assert oauth_service.calls == [{"method": "has_connection", "telegram_user_id": "99"}]
    assert len(today_brief_service.calls) == 1
