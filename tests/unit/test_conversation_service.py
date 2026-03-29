from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.action_log import ActionLog
from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.memory_fact import MemoryFact
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.domain.schemas import PlannedConversation
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickTask
from ticktick_telegram_assistant.services.memory_service import MemoryService
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


class FakeTimezoneResolver:
    def __init__(self, *, location_timezone: str | None = None, text_timezone: str | None = None) -> None:
        self.location_timezone = location_timezone
        self.text_timezone = text_timezone
        self.location_calls: list[dict] = []
        self.text_calls: list[str] = []

    async def resolve_from_location(self, *, latitude: float, longitude: float) -> str | None:
        self.location_calls.append({"latitude": latitude, "longitude": longitude})
        return self.location_timezone

    def resolve_from_text(self, text: str) -> str | None:
        self.text_calls.append(text)
        return self.text_timezone


class FakeTickTickClient:
    def __init__(self, tasks: list[TickTickTask] | None = None) -> None:
        self.tasks = tasks or []
        self.calls: list[dict] = []

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
        return list(self.tasks)


class FakeTaskCommandService:
    def __init__(
        self,
        reply_text: str = "好，我已经替你记进 TickTick 了。",
        created_task_id: str | None = None,
    ) -> None:
        self.reply_text = reply_text
        self.created_task_id = created_task_id
        self.calls: list[dict] = []

    async def execute_action(self, *, telegram_user_id: str, action, now=None) -> str:
        if action.action_type == "create_task" and self.created_task_id:
            action.target_task_id = self.created_task_id
        self.calls.append({"telegram_user_id": telegram_user_id, "action": action, "now": now})
        return self.reply_text


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


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
async def test_handle_update_recognizes_today_todo_question_as_today_brief() -> None:
    planner = FakePlanner(PlannedConversation())
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    today_brief_service = FakeTodayBriefService("今天重点：\n- 先做 A")
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        today_brief_service=today_brief_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 4_1,
            "message": {
                "message_id": 10_1,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "我今天要做什么",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["今天重点：\n- 先做 A"]
    assert planner.contexts == []
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
async def test_handle_update_writes_action_log_for_task_execution() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(User(telegram_user_id="99", display_name="Jiaxin"))
        session.commit()

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
    task_command_service = FakeTaskCommandService("好，我已经替你记进 TickTick 了：周六 15:00 给导师发邮件")
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=MemoryService(session_factory=session_factory),
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 5_1,
            "message": {
                "message_id": 11_1,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师发邮件",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    with session_factory() as session:
        row = session.query(ActionLog).one()

    assert row.source_message_id == "111"
    assert row.original_text == "明天下午3点提醒我给导师发邮件"
    assert row.status == "task_action"
    assert row.parsed_plan_json["actions"][0]["action_type"] == "create_task"
    assert row.execution_result_json["reply_texts"] == ["好，我已经替你记进 TickTick 了：周六 15:00 给导师发邮件"]


@pytest.mark.asyncio
async def test_handle_update_writes_action_log_for_confirmation_pending() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(User(telegram_user_id="99", display_name="Jiaxin"))
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[],
            requires_confirmation=True,
            assistant_reply="你是指周二那条，还是周三那条？",
        )
    )
    service = ConversationService(
        planner=planner,
        session_factory=session_factory,
        memory_service=MemoryService(session_factory=session_factory),
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 5_2,
            "message": {
                "message_id": 112,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "改到明天下午",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["你是指周二那条，还是周三那条？"]
    with session_factory() as session:
        row = session.query(ActionLog).one()

    assert row.status == "confirmation_pending"
    assert row.parsed_plan_json["requires_confirmation"] is True
    assert row.execution_result_json["reply_texts"] == ["你是指周二那条，还是周三那条？"]


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
async def test_handle_update_yes_reply_uses_saved_today_brief_confirmation() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    memory_service = MemoryService(session_factory=session_factory)
    memory_service.replace_active_context(
        user_id=user_id,
        context_type="pending_query",
        payload_json={"kind": "today_brief"},
    )
    today_brief_service = FakeTodayBriefService("今天重点：\n- 先做 A")
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    service = ConversationService(
        ticktick_oauth_service=oauth_service,
        today_brief_service=today_brief_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 6_1,
            "message": {
                "message_id": 12_1,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "对",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["今天重点：\n- 先做 A"]
    assert memory_service.get_active_context(user_id=user_id, context_type="pending_query") is None


@pytest.mark.asyncio
async def test_handle_update_saves_today_brief_query_prompt_for_follow_up_yes() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    planner = FakePlanner(
        [
            PlannedConversation(assistant_reply="要我帮你列出 TickTick 中今天的任务吗？"),
            PlannedConversation(),
        ]
    )
    memory_service = MemoryService(session_factory=session_factory)
    today_brief_service = FakeTodayBriefService("今天重点：\n- 先做 A")
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        today_brief_service=today_brief_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    first_update = TelegramUpdate.model_validate(
        {
            "update_id": 6_2,
            "message": {
                "message_id": 12_2,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "帮我看一下",
            },
        }
    )
    second_update = TelegramUpdate.model_validate(
        {
            "update_id": 6_3,
            "message": {
                "message_id": 12_3,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "对",
            },
        }
    )

    first_replies = await service.handle_update(first_update)
    second_replies = await service.handle_update(second_update)

    assert [reply.text for reply in first_replies] == ["要我帮你列出 TickTick 中今天的任务吗？"]
    assert [reply.text for reply in second_replies] == ["今天重点：\n- 先做 A"]
    assert memory_service.get_active_context(user_id=user_id, context_type="pending_query") is None


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
async def test_handle_update_processes_memo_cleanup_schedule_reply() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        session.add(
            ReminderEvent(
                user_id=user.id,
                event_type="memo_cleanup",
                scheduled_at=datetime.fromisoformat("2026-03-29T17:00:00-07:00"),
                dedupe_key="memo-cleanup-1",
                status="sent",
                payload_json={
                    "candidate_tasks": [
                        {"task_id": "memo-1", "title": "整理发票", "description": "周末"},
                    ]
                },
            )
        )
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "update_task",
                    "payload": {
                        "due_at": "2026-03-30T10:00:00-07:00",
                        "semantic_type": "explicit_time",
                    },
                }
            ]
        )
    )
    task_command_service = FakeTaskCommandService("好，我已经替你改好了：周一 10:00 整理发票")
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=MemoryService(session_factory=session_factory),
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 7_1,
            "message": {
                "message_id": 13_1,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "把第1条安排到明天上午10点",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["好，我已经替你改好了：周一 10:00 整理发票"]
    assert len(task_command_service.calls) == 1
    action = task_command_service.calls[0]["action"]
    assert action.action_type == "update_task"
    assert action.target_task_id == "memo-1"
    assert action.payload["match_title"] == "整理发票"
    assert action.payload["semantic_type"] == "explicit_time"


@pytest.mark.asyncio
async def test_handle_update_processes_memo_cleanup_keep_reply() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        session.add(
            ReminderEvent(
                user_id=user.id,
                event_type="memo_cleanup",
                scheduled_at=datetime.fromisoformat("2026-03-29T17:00:00-07:00"),
                dedupe_key="memo-cleanup-keep",
                status="sent",
                payload_json={
                    "candidate_tasks": [
                        {"task_id": "memo-1", "title": "整理发票", "description": "周末"},
                    ]
                },
            )
        )
        session.commit()

    task_command_service = FakeTaskCommandService()
    service = ConversationService(
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=MemoryService(session_factory=session_factory),
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 7_2,
            "message": {
                "message_id": 13_2,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "先都留着",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["好，那我先继续把这些留在备忘池里，等你想安排时间时再叫我。"]
    assert task_command_service.calls == []


@pytest.mark.asyncio
async def test_handle_update_requests_confirmation_for_overlapping_span_task() -> None:
    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "create_task",
                    "payload": {
                        "title": "项目复盘",
                        "semantic_type": "explicit_time",
                        "start_at": "2026-03-28T15:00:00-07:00",
                        "duration_minutes": 60,
                    },
                }
            ]
        )
    )
    oauth_service = FakeTickTickOAuthService(connected=True, auth_url=None)
    ticktick_client = FakeTickTickClient(
        tasks=[
            TickTickTask(
                id="task-1",
                projectId="telegram-inbox",
                title="导师会议",
                startDate="2026-03-28T15:30:00.000-0700",
                dueDate="2026-03-28T16:30:00.000-0700",
                status=0,
            )
        ]
    )
    task_command_service = FakeTaskCommandService()
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
                ticktick_access_token="access-token",
            )
        )
        session.commit()

    service = ConversationService(
        planner=planner,
        ticktick_oauth_service=oauth_service,
        task_command_service=task_command_service,
        ticktick_client=ticktick_client,
        session_factory=session_factory,
        memory_service=MemoryService(session_factory=session_factory),
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 7_3,
            "message": {
                "message_id": 13_3,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点到4点项目复盘",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "撞上了" in replies[0].text
    assert task_command_service.calls == []


@pytest.mark.asyncio
async def test_handle_update_persists_planner_confirmation_and_resumes_it() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    planner = FakePlanner(
        [
            PlannedConversation(
                requires_confirmation=True,
                assistant_reply="这个改动看起来有点高风险，要我继续吗？",
            ),
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
            ),
        ]
    )
    task_command_service = FakeTaskCommandService("好，我已经替你改好了：周六 15:00 weekly sync")
    memory_service = MemoryService(session_factory=session_factory)
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    first_update = TelegramUpdate.model_validate(
        {
            "update_id": 7_1,
            "message": {
                "message_id": 13_1,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "把 weekly sync 改到明天下午3点",
            },
        }
    )
    second_update = TelegramUpdate.model_validate(
        {
            "update_id": 7_2,
            "message": {
                "message_id": 13_2,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "继续",
            },
        }
    )

    first_replies = await service.handle_update(first_update)
    second_replies = await service.handle_update(second_update)

    assert [reply.text for reply in first_replies] == ["这个改动看起来有点高风险，要我继续吗？"]
    assert [reply.text for reply in second_replies] == ["好，我已经替你改好了：周六 15:00 weekly sync"]
    assert planner.contexts[1].user_text == "把 weekly sync 改到明天下午3点\n用户确认：继续"
    assert len(task_command_service.calls) == 1
    assert task_command_service.calls[0]["action"].action_type == "update_task"
    assert memory_service.get_active_context(user_id=user_id, context_type="pending_confirmation") is None


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
    task_command_service = FakeTaskCommandService(created_task_id="task-created-1")
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
    assert [call["action"].target_task_id for call in task_command_service.calls] == [
        "task-created-1",
        "task-created-1",
        "task-created-1",
        "task-created-1",
    ]


@pytest.mark.asyncio
async def test_handle_update_processes_following_batch_lines_after_confirmation_prompt() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.commit()

    planner = FakePlanner(
        [
            PlannedConversation(
                requires_confirmation=True,
                assistant_reply="这条改动有点高风险，要我继续吗？",
            ),
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
        ]
    )
    task_command_service = FakeTaskCommandService("ok")
    memory_service = MemoryService(session_factory=session_factory)
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 10_1,
            "message": {
                "message_id": 16_1,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "把 weekly sync 改到明天下午3点\n明天下午4点提醒我给导师发邮件",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["这条改动有点高风险，要我继续吗？\nok"]
    assert planner.contexts[0].user_text == "把 weekly sync 改到明天下午3点"
    assert planner.contexts[1].user_text == "明天下午4点提醒我给导师发邮件"
    assert len(task_command_service.calls) == 1
    assert task_command_service.calls[0]["action"].action_type == "create_task"
    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
    context = memory_service.get_active_context(user_id=user.id, context_type="pending_confirmation")
    assert context is not None
    assert context.payload_json["kind"] == "planner_confirmation"


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


@pytest.mark.asyncio
async def test_handle_update_executes_evening_review_reply_from_saved_reminder_context() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        session.add(
            ReminderEvent(
                user_id=user.id,
                ticktick_task_id=None,
                event_type="evening_review",
                scheduled_at=datetime(2026, 3, 27, 23, 30, tzinfo=timezone.utc),
                dedupe_key="evening-review-2026-03-27",
                status="sent",
                sent_at=datetime(2026, 3, 28, 6, 30, tzinfo=timezone.utc),
                payload_json={
                    "candidate_tasks": [
                        {"task_id": "task-1", "title": "写周报"},
                        {"task_id": "task-2", "title": "回导师邮件"},
                        {"task_id": "task-3", "title": "整理实验记录"},
                    ]
                },
            )
        )
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "update_task",
                    "payload": {
                        "match_title": "整理实验记录",
                        "due_at": "2026-03-28T15:00:00-07:00",
                    },
                }
            ]
        )
    )

    class SequencedTaskCommandService(FakeTaskCommandService):
        async def execute_action(self, *, telegram_user_id: str, action, now=None) -> str:
            self.calls.append({"telegram_user_id": telegram_user_id, "action": action, "now": now})
            mapping = {
                "complete_task": f"done:{action.target_task_id}",
                "update_task": f"updated:{action.target_task_id}",
            }
            return mapping[action.action_type]

    task_command_service = SequencedTaskCommandService()
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 12,
            "message": {
                "message_id": 18,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "前两个做完了，第三个改到周四下午",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["done:task-1\ndone:task-2\nupdated:task-3"]
    assert [call["action"].action_type for call in task_command_service.calls] == [
        "complete_task",
        "complete_task",
        "update_task",
    ]
    assert [call["action"].target_task_id for call in task_command_service.calls] == [
        "task-1",
        "task-2",
        "task-3",
    ]
    assert planner.contexts[0].user_text == "任务“整理实验记录”改到周四下午"


@pytest.mark.asyncio
async def test_handle_update_schedules_snooze_from_latest_reminder_context() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        session.add(
            ReminderEvent(
                user_id=user.id,
                ticktick_task_id="task-1",
                event_type="prestart_reminder",
                scheduled_at=datetime(2026, 3, 27, 14, 55, tzinfo=timezone.utc),
                dedupe_key="prestart-task-1",
                status="sent",
                sent_at=datetime(2026, 3, 27, 14, 55, tzinfo=timezone.utc),
                payload_json={
                    "task_id": "task-1",
                    "title": "给导师A发邮件",
                    "task_due_at": "2026-03-29T15:00:00-07:00",
                },
            )
        )
        session.commit()

    service = ConversationService(session_factory=session_factory)
    update = TelegramUpdate.model_validate(
        {
            "update_id": 13,
            "message": {
                "message_id": 19,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "1小时后再提醒我",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "1小时后" in replies[0].text
    with session_factory() as session:
        events = session.query(ReminderEvent).order_by(ReminderEvent.id).all()
        assert len(events) == 2
        snoozed = events[-1]
        assert snoozed.event_type == "snoozed_reminder"
        assert snoozed.status == "pending"
        assert snoozed.ticktick_task_id == "task-1"
        assert snoozed.payload_json["task_due_at"] == "2026-03-29T15:00:00-07:00"
        assert snoozed.scheduled_at.isoformat().startswith("2026-03-29T23:00:00")


@pytest.mark.asyncio
async def test_handle_update_updates_timezone_from_location() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
            )
        )
        session.commit()

    timezone_resolver = FakeTimezoneResolver(location_timezone="America/New_York")
    service = ConversationService(
        session_factory=session_factory,
        timezone_resolver=timezone_resolver,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 14,
            "message": {
                "message_id": 20,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "location": {"latitude": 40.7128, "longitude": -74.0060},
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "America/New_York" in replies[0].text
    assert timezone_resolver.location_calls == [{"latitude": 40.7128, "longitude": -74.006}]
    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
        assert user.current_timezone == "America/New_York"
        assert user.timezone_source == "location"


@pytest.mark.asyncio
async def test_handle_update_updates_timezone_from_text() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
            )
        )
        session.commit()

    timezone_resolver = FakeTimezoneResolver(text_timezone="Asia/Shanghai")
    service = ConversationService(
        session_factory=session_factory,
        timezone_resolver=timezone_resolver,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 15,
            "message": {
                "message_id": 21,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "以后按北京时间提醒我",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "Asia/Shanghai" in replies[0].text
    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
        assert user.current_timezone == "Asia/Shanghai"
        assert user.timezone_source == "text"


@pytest.mark.asyncio
async def test_handle_update_builds_planner_context_from_memory_service() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    memory_service = MemoryService(session_factory=session_factory)
    memory_service.upsert_fact(
        user_id=user_id,
        key="fun list",
        value_json={"canonical_name": "fun"},
        memory_type=MemoryType.ALIAS_MAPPING,
    )
    planner = FakePlanner(PlannedConversation(assistant_reply="ok"))
    service = ConversationService(
        planner=planner,
        session_factory=session_factory,
        memory_service=memory_service,
    )

    update = TelegramUpdate.model_validate(
        {
            "update_id": 15,
            "message": {
                "message_id": 21,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "放到 fun 那个 list",
            },
        }
    )

    await service.handle_update(update)

    assert planner.contexts[0].recent_conversation_summaries == ["移动到 list：fun"]
    assert planner.contexts[0].memory_items == [
        "conversation_summary: 移动到 list：fun",
        "alias_mapping: fun list -> fun",
    ]


@pytest.mark.asyncio
async def test_handle_update_records_time_expression_and_alias_mappings_after_successful_create() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
        )
        session.add(user)
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "create_task",
                    "payload": {
                        "title": "给导师发邮件",
                        "semantic_type": "explicit_time",
                        "due_at": "2026-03-28T15:00:00-07:00",
                        "raw_nl_time": "明天下午3点",
                        "list_name": "fun list",
                        "tags": ["work"],
                    },
                }
            ]
        )
    )
    memory_service = MemoryService(session_factory=session_factory)
    task_command_service = FakeTaskCommandService(
        reply_text="好，我已经替你记进 TickTick 了：周六 15:00 给导师发邮件",
        created_task_id="task-memory-1",
    )
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 15_1,
            "message": {
                "message_id": 21_1,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师发邮件，放到 fun list，打上 work 标签",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["好，我已经替你记进 TickTick 了：周六 15:00 给导师发邮件"]
    with session_factory() as session:
        facts = session.query(MemoryFact).order_by(MemoryFact.id.asc()).all()

    by_type_and_key = {(fact.memory_type, fact.key): fact for fact in facts}
    assert by_type_and_key[("time_expression", "明天下午3点")].value_json["resolved_due_at"] == "2026-03-28T15:00:00-07:00"
    assert by_type_and_key[("time_expression", "明天下午3点")].source_type == "task_execution"
    assert by_type_and_key[("alias_mapping", "fun list")].value_json == {"canonical_name": "fun list", "kind": "list"}
    assert by_type_and_key[("alias_mapping", "work")].value_json == {"canonical_name": "work", "kind": "tag"}


@pytest.mark.asyncio
async def test_handle_update_records_disambiguation_pattern_when_selection_is_confirmed() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    memory_service = MemoryService(session_factory=session_factory)
    memory_service.replace_active_context(
        user_id=user_id,
        context_type="pending_confirmation",
        payload_json={
            "kind": "task_disambiguation",
            "candidate_tasks": [
                {"task_id": "existing-1", "title": "给导师A发邮件", "when": "周一 15:00"},
                {"task_id": "existing-2", "title": "给导师A发邮件", "when": "周二 15:00"},
            ],
            "original_action": {
                "action_type": "update_task",
                "payload": {
                    "match_title": "给导师A发邮件",
                    "description": "记得带附件",
                },
            },
        },
    )
    task_command_service = FakeTaskCommandService("updated")
    service = ConversationService(
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 15_2,
            "message": {
                "message_id": 21_2,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "第二条",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["updated"]
    with session_factory() as session:
        fact = (
            session.query(MemoryFact)
            .filter(
                MemoryFact.user_id == user_id,
                MemoryFact.memory_type == MemoryType.DISAMBIGUATION_PATTERN.value,
                MemoryFact.key == "给导师A发邮件",
            )
            .one()
        )

    assert fact.value_json["selected_task_id"] == "existing-2"
    assert fact.value_json["selected_title"] == "给导师A发邮件"
    context = memory_service.build_context(
        user_id=user_id,
        text="给导师A发邮件再补一句说明",
        current_timezone="America/Los_Angeles",
    )
    assert any(item.startswith("disambiguation_pattern: 给导师A发邮件 ->") for item in context.memory_items)


@pytest.mark.asyncio
async def test_handle_update_persists_active_task_context_in_memory_service() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "create_task",
                    "payload": {
                        "title": "给导师发邮件",
                        "semantic_type": "memo",
                    },
                }
            ]
        )
    )
    memory_service = MemoryService(session_factory=session_factory)
    task_command_service = FakeTaskCommandService(created_task_id="task-memory-1")
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 16,
            "message": {
                "message_id": 22,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "提醒我给导师发邮件",
            },
        }
    )

    await service.handle_update(update)

    context = memory_service.get_active_context(user_id=user_id, context_type="active_task")
    assert context is not None
    assert context.payload_json == {"title": "给导师发邮件", "task_id": "task-memory-1"}


@pytest.mark.asyncio
async def test_handle_update_requests_confirmation_for_duplicate_create() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
                ticktick_access_token="access-token",
            )
        )
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "create_task",
                    "payload": {
                        "title": "给导师A发邮件",
                        "semantic_type": "explicit_time",
                        "due_at": "2026-03-29T15:00:00-07:00",
                    },
                }
            ]
        )
    )
    task_command_service = FakeTaskCommandService("should-not-run")
    ticktick_client = FakeTickTickClient(
        tasks=[
            TickTickTask(
                id="existing-1",
                projectId="telegram-inbox",
                title="给导师A发邮件",
                dueDate="2026-03-29T15:00:00.000-0700",
                status=0,
            )
        ]
    )
    memory_service = MemoryService(session_factory=session_factory)
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        memory_service=memory_service,
    )

    update = TelegramUpdate.model_validate(
        {
            "update_id": 17,
            "message": {
                "message_id": 23,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师A发邮件",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "很像" in replies[0].text
    assert task_command_service.calls == []
    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
    context = memory_service.get_active_context(user_id=user.id, context_type="pending_confirmation")
    assert context is not None
    assert context.payload_json["kind"] == "duplicate_create"
    assert context.payload_json["candidate_task"]["task_id"] == "existing-1"


@pytest.mark.asyncio
async def test_handle_update_executes_confirmation_reply_for_duplicate_create_merge() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    memory_service = MemoryService(session_factory=session_factory)
    memory_service.replace_active_context(
        user_id=user_id,
        context_type="pending_confirmation",
        payload_json={
            "kind": "duplicate_create",
            "candidate_task": {"task_id": "existing-1", "title": "给导师A发邮件"},
            "original_action": {
                "action_type": "create_task",
                "payload": {
                    "title": "给导师A发邮件",
                    "description": "记得带附件",
                    "semantic_type": "memo",
                },
            },
        },
    )

    task_command_service = FakeTaskCommandService("merged")
    service = ConversationService(
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 18,
            "message": {
                "message_id": 24,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "合并到原来那条",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["merged"]
    assert len(task_command_service.calls) == 1
    action = task_command_service.calls[0]["action"]
    assert action.action_type == "update_task"
    assert action.target_task_id == "existing-1"
    assert action.payload["match_title"] == "给导师A发邮件"
    assert action.payload["description"] == "记得带附件"
    assert memory_service.get_active_context(user_id=user_id, context_type="pending_confirmation") is None


@pytest.mark.asyncio
async def test_handle_update_requests_confirmation_for_time_conflict() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
                ticktick_access_token="access-token",
            )
        )
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "create_task",
                    "payload": {
                        "title": "给导师A发邮件",
                        "semantic_type": "explicit_time",
                        "due_at": "2026-03-29T15:00:00-07:00",
                    },
                }
            ]
        )
    )
    ticktick_client = FakeTickTickClient(
        tasks=[
            TickTickTask(
                id="existing-2",
                projectId="telegram-inbox",
                title="周会",
                dueDate="2026-03-29T15:30:00.000-0700",
                status=0,
            )
        ]
    )
    task_command_service = FakeTaskCommandService("should-not-run")
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        memory_service=MemoryService(session_factory=session_factory),
    )

    update = TelegramUpdate.model_validate(
        {
            "update_id": 19,
            "message": {
                "message_id": 25,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "明天下午3点提醒我给导师A发邮件",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "撞上了" in replies[0].text
    assert "周会" in replies[0].text
    assert task_command_service.calls == []


@pytest.mark.asyncio
async def test_handle_update_requests_confirmation_for_duplicate_rename() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
                ticktick_access_token="access-token",
            )
        )
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "update_task",
                    "payload": {
                        "match_title": "写周报草稿",
                        "title": "写周报",
                    },
                }
            ]
        )
    )
    ticktick_client = FakeTickTickClient(
        tasks=[
            TickTickTask(
                id="target-1",
                projectId="telegram-inbox",
                title="写周报草稿",
                dueDate="2026-03-29T10:00:00.000-0700",
                status=0,
            ),
            TickTickTask(
                id="existing-dup",
                projectId="telegram-inbox",
                title="写周报",
                dueDate="2026-03-30T10:00:00.000-0700",
                status=0,
            ),
        ]
    )
    task_command_service = FakeTaskCommandService("should-not-run")
    memory_service = MemoryService(session_factory=session_factory)
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        memory_service=memory_service,
    )

    update = TelegramUpdate.model_validate(
        {
            "update_id": 19,
            "message": {
                "message_id": 25,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "把写周报草稿改成写周报",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "很像" in replies[0].text
    assert "写周报" in replies[0].text
    assert task_command_service.calls == []
    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
    context = memory_service.get_active_context(user_id=user.id, context_type="pending_confirmation")
    assert context is not None
    assert context.payload_json["candidate_task"]["task_id"] == "existing-dup"


@pytest.mark.asyncio
async def test_handle_update_requests_numbered_confirmation_for_ambiguous_update() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        session.add(
            User(
                telegram_user_id="99",
                display_name="Jiaxin",
                current_timezone="America/Los_Angeles",
                ticktick_access_token="access-token",
            )
        )
        session.commit()

    planner = FakePlanner(
        PlannedConversation(
            actions=[
                {
                    "action_type": "update_task",
                    "payload": {
                        "match_title": "给导师A发邮件",
                        "description": "记得带附件",
                    },
                }
            ]
        )
    )
    ticktick_client = FakeTickTickClient(
        tasks=[
            TickTickTask(
                id="existing-1",
                projectId="telegram-inbox",
                title="给导师A发邮件",
                dueDate="2026-03-29T15:00:00.000-0700",
                status=0,
            ),
            TickTickTask(
                id="existing-2",
                projectId="telegram-inbox",
                title="给导师A发邮件",
                dueDate="2026-03-30T15:00:00.000-0700",
                status=0,
            ),
        ]
    )
    memory_service = MemoryService(session_factory=session_factory)
    task_command_service = FakeTaskCommandService("should-not-run")
    service = ConversationService(
        planner=planner,
        task_command_service=task_command_service,
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        memory_service=memory_service,
    )

    update = TelegramUpdate.model_validate(
        {
            "update_id": 20,
            "message": {
                "message_id": 26,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "给导师A发邮件再补一句说明：记得带附件",
            },
        }
    )

    replies = await service.handle_update(update)

    assert len(replies) == 1
    assert "你是指哪一条" in replies[0].text
    assert "1." in replies[0].text
    assert "2." in replies[0].text
    assert task_command_service.calls == []
    with session_factory() as session:
        user = session.query(User).filter(User.telegram_user_id == "99").one()
    context = memory_service.get_active_context(user_id=user.id, context_type="pending_confirmation")
    assert context is not None
    assert context.payload_json["kind"] == "task_disambiguation"
    assert len(context.payload_json["candidate_tasks"]) == 2


@pytest.mark.asyncio
async def test_handle_update_executes_numbered_reply_for_task_disambiguation() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        user = User(
            telegram_user_id="99",
            display_name="Jiaxin",
            current_timezone="America/Los_Angeles",
            ticktick_access_token="access-token",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    memory_service = MemoryService(session_factory=session_factory)
    memory_service.replace_active_context(
        user_id=user_id,
        context_type="pending_confirmation",
        payload_json={
            "kind": "task_disambiguation",
            "candidate_tasks": [
                {"task_id": "existing-1", "title": "给导师A发邮件"},
                {"task_id": "existing-2", "title": "给导师A发邮件"},
            ],
            "original_action": {
                "action_type": "update_task",
                "payload": {
                    "match_title": "给导师A发邮件",
                    "description": "记得带附件",
                },
            },
        },
    )
    task_command_service = FakeTaskCommandService("updated")
    service = ConversationService(
        task_command_service=task_command_service,
        session_factory=session_factory,
        memory_service=memory_service,
    )
    update = TelegramUpdate.model_validate(
        {
            "update_id": 21,
            "message": {
                "message_id": 27,
                "from": {"id": 99},
                "chat": {"id": 99, "type": "private"},
                "text": "第二条",
            },
        }
    )

    replies = await service.handle_update(update)

    assert [reply.text for reply in replies] == ["updated"]
    assert len(task_command_service.calls) == 1
    action = task_command_service.calls[0]["action"]
    assert action.action_type == "update_task"
    assert action.target_task_id == "existing-2"
    assert action.payload["match_title"] == "给导师A发邮件"
    assert memory_service.get_active_context(user_id=user_id, context_type="pending_confirmation") is None
