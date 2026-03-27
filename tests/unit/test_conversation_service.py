import pytest

from ticktick_telegram_assistant.domain.schemas import PlannedConversation
from ticktick_telegram_assistant.services.conversation_service import (
    ConversationService,
    TelegramUpdate,
)


class FakePlanner:
    def __init__(self, planned: PlannedConversation | list[PlannedConversation]) -> None:
        self._planned = planned
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        if isinstance(self._planned, list):
            return self._planned.pop(0)
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


class FakeTaskCommandService:
    def __init__(self, reply_text: str = "好，我已经替你记进 TickTick 了。") -> None:
        self.reply_text = reply_text
        self.calls: list[dict] = []

    async def execute_action(self, *, telegram_user_id: str, action, now=None) -> str:
        self.calls.append({"telegram_user_id": telegram_user_id, "action": action, "now": now})
        return self.reply_text


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


@pytest.mark.asyncio
async def test_handle_update_executes_create_action_when_ticktick_connected() -> None:
    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "create_task",
                    "payload": {
                        "title": "给导师发邮件",
                        "semantic_type": "explicit_time",
                        "due_at": "2026-03-28T15:00:00-07:00",
                    },
                }
            ]
        )
    )
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    task_command_service = FakeTaskCommandService("好，我已经替你记进 TickTick 了：周六 15:00 给导师发邮件")
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        task_command_service=task_command_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 5,
            "message": {
                "message_id": 11,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师发邮件",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["好，我已经替你记进 TickTick 了：周六 15:00 给导师发邮件"]
    assert oauth_service.calls == [{"method": "has_connection", "telegram_user_id": "99"}]
    assert planner.contexts[0].user_text == "明天下午3点提醒我给导师发邮件"
    assert len(task_command_service.calls) == 1
    assert task_command_service.calls[0]["telegram_user_id"] == "99"
    assert task_command_service.calls[0]["action"].action_type == "create_task"


@pytest.mark.asyncio
async def test_handle_update_executes_complete_action_when_ticktick_connected() -> None:
    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "complete_task",
                    "payload": {"title": "给导师发邮件"},
                }
            ]
        )
    )
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    task_command_service = FakeTaskCommandService("好，这条我帮你勾完成了：给导师发邮件")
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        task_command_service=task_command_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 6,
            "message": {
                "message_id": 12,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "给导师发邮件做完了",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["好，这条我帮你勾完成了：给导师发邮件"]
    assert len(task_command_service.calls) == 1
    assert task_command_service.calls[0]["action"].action_type == "complete_task"


@pytest.mark.asyncio
async def test_handle_update_executes_update_action_when_ticktick_connected() -> None:
    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "update_task",
                    "payload": {
                        "match_title": "weekly sync",
                        "due_at": "2026-03-28T15:00:00-07:00",
                    },
                }
            ]
        )
    )
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    task_command_service = FakeTaskCommandService("好，我已经替你改好了：周六 15:00 weekly sync")
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        task_command_service=task_command_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 7,
            "message": {
                "message_id": 13,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "把 weekly sync 改到明天下午3点",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["好，我已经替你改好了：周六 15:00 weekly sync"]
    assert len(task_command_service.calls) == 1
    assert task_command_service.calls[0]["action"].action_type == "update_task"


@pytest.mark.asyncio
async def test_handle_update_reuses_last_task_context_for_follow_up_edit() -> None:
    planner = FakePlanner(
        [
            PlannedConversation(
                actions=[
                    {
                        "action_type": "create_task",
                        "payload": {
                            "title": "给导师发邮件",
                            "semantic_type": "explicit_time",
                            "due_at": "2026-03-28T15:00:00-07:00",
                        },
                    }
                ]
            ),
            PlannedConversation(
                actions=[
                    {
                        "action_type": "update_task",
                        "payload": {
                            "match_title": "给导师发邮件",
                            "due_at": "2026-03-29T15:00:00-07:00",
                        },
                    }
                ]
            ),
        ]
    )
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    task_command_service = FakeTaskCommandService()
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        task_command_service=task_command_service,
    )

    create_update = TelegramUpdate.model_validate(
        {
            "update_id": 8,
            "message": {
                "message_id": 14,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师发邮件",
            },
        }
    )
    follow_up_update = TelegramUpdate.model_validate(
        {
            "update_id": 9,
            "message": {
                "message_id": 15,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "改到后天下午3点",
            },
        }
    )

    await service.handle_update(create_update)
    await service.handle_update(follow_up_update)

    assert planner.contexts[1].user_text == "任务“给导师发邮件”改到后天下午3点"


@pytest.mark.asyncio
async def test_handle_update_processes_multiline_batch_sequentially() -> None:
    planner = FakePlanner(
        [
            PlannedConversation(
                actions=[
                    {
                        "action_type": "create_task",
                        "payload": {
                            "title": "给导师发邮件",
                            "semantic_type": "explicit_time",
                            "due_at": "2026-03-28T15:00:00-07:00",
                        },
                    }
                ]
            ),
            PlannedConversation(
                actions=[
                    {
                        "action_type": "update_task",
                        "payload": {
                            "match_title": "给导师发邮件",
                            "due_at": "2026-03-29T15:00:00-07:00",
                        },
                    }
                ]
            ),
            PlannedConversation(
                actions=[
                    {
                        "action_type": "update_task",
                        "payload": {
                            "match_title": "给导师发邮件",
                            "description": "记得带附件",
                            "description_mode": "append",
                        },
                    }
                ]
            ),
            PlannedConversation(
                actions=[
                    {
                        "action_type": "update_task",
                        "payload": {
                            "match_title": "给导师发邮件",
                            "list_name": "fun",
                        },
                    }
                ]
            ),
        ]
    )
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    task_command_service = FakeTaskCommandService()
    task_command_service.reply_text = "ok"
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        task_command_service=task_command_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 10,
            "message": {
                "message_id": 16,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师发邮件\n改到后天下午3点\n再补一句说明：记得带附件\n放到 fun 那个 list",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["ok\nok\nok\nok"]
    assert [context.user_text for context in planner.contexts] == [
        "明天下午3点提醒我给导师发邮件",
        "任务“给导师发邮件”改到后天下午3点",
        "任务“给导师发邮件”再补一句说明：记得带附件",
        "任务“给导师发邮件”放到 fun 那个 list",
    ]


@pytest.mark.asyncio
async def test_handle_update_returns_single_oauth_prompt_for_multiline_batch_when_disconnected() -> None:
    planner = FakePlanner(PlannedConversation())
    oauth_service = FakeTickTickOAuthService(
        connected=False,
        auth_url="https://ticktick.com/oauth/authorize?state=abc",
    )
    service = ConversationService(planner=planner, ticktick_oauth_service=oauth_service)
    update = TelegramUpdate.model_validate(
        {
            "update_id": 11,
            "message": {
                "message_id": 17,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师发邮件\n改到后天下午3点\n再补一句说明：记得带附件\n放到 fun 那个 list",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == [
        "我还没连上你的 TickTick。先点这个链接授权一下，我连好后就能继续帮你了：https://ticktick.com/oauth/authorize?state=abc"
    ]
    assert planner.contexts == []
    assert oauth_service.calls == [
        {"method": "has_connection", "telegram_user_id": "99"},
        {
            "method": "create_authorization_url",
            "telegram_user_id": "99",
            "display_name": None,
        },
    ]
