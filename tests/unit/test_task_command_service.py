from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.schemas import PlannedAction
from ticktick_telegram_assistant.integrations.ticktick_client import (
    TickTickProject,
    TickTickTask,
)


class FakeTickTickClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.tasks: list[TickTickTask] = []
        self.fail_on_methods: dict[str, Exception] = {}

    async def list_projects(self, *, access_token: str) -> list[TickTickProject]:
        self.calls.append({"method": "list_projects", "access_token": access_token})
        if "list_projects" in self.fail_on_methods:
            raise self.fail_on_methods["list_projects"]
        return [
            TickTickProject(id="telegram-inbox", name="Telegram Inbox", kind="TASK"),
            TickTickProject(id="fun", name="fun", kind="TASK"),
        ]

    async def create_task(self, *, access_token: str, task) -> TickTickTask:
        payload = task.model_dump(exclude_none=True)
        self.calls.append({"method": "create_task", "access_token": access_token, "task": payload})
        if "create_task" in self.fail_on_methods:
            raise self.fail_on_methods["create_task"]
        return TickTickTask(
            id="task-1",
            projectId=payload["projectId"],
            title=payload["title"],
            desc=payload.get("desc", ""),
            dueDate=payload.get("dueDate"),
            status=0,
        )

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
        if "list_tasks" in self.fail_on_methods:
            raise self.fail_on_methods["list_tasks"]
        return list(self.tasks)

    async def complete_task(self, *, access_token: str, project_id: str, task_id: str) -> None:
        self.calls.append(
            {
                "method": "complete_task",
                "access_token": access_token,
                "project_id": project_id,
                "task_id": task_id,
            }
        )
        if "complete_task" in self.fail_on_methods:
            raise self.fail_on_methods["complete_task"]

    async def update_task(self, *, access_token: str, task_id: str, patch) -> TickTickTask:
        payload = patch.model_dump(exclude_none=True)
        self.calls.append(
            {
                "method": "update_task",
                "access_token": access_token,
                "task_id": task_id,
                "patch": payload,
            }
        )
        if "update_task" in self.fail_on_methods:
            raise self.fail_on_methods["update_task"]
        return TickTickTask(
            id=task_id,
            projectId=payload["projectId"],
            title=payload.get("title", "unchanged"),
            desc=payload.get("desc", ""),
            dueDate=payload.get("dueDate"),
            status=0,
        )


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def make_ticktick_500_error(path: str = "/open/v1/project") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://api.ticktick.com{path}")
    response = httpx.Response(500, request=request)
    return httpx.HTTPStatusError("temporary failure", request=request, response=response)


@pytest.mark.asyncio
async def test_execute_action_creates_explicit_time_task_and_shadow() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    action = PlannedAction(
        action_type="create_task",
        payload={
            "title": "给导师发邮件",
            "description": "补实验结果",
            "semantic_type": "explicit_time",
            "due_at": "2026-03-28T15:00:00-07:00",
        },
    )
    reply = await service.execute_action(
        telegram_user_id="99",
        action=action,
    )

    assert "给导师发邮件" in reply
    assert "15:00" in reply
    assert action.target_task_id == "task-1"
    assert client.calls == [
        {"method": "list_projects", "access_token": "access-token"},
        {
            "method": "create_task",
            "access_token": "access-token",
            "task": {
                "title": "给导师发邮件",
                "projectId": "telegram-inbox",
                "desc": "补实验结果",
                "dueDate": "2026-03-28T15:00:00-0700",
                "timeZone": "America/Los_Angeles",
                "content": "",
                "reminders": [],
                "items": [],
            },
        },
    ]

    with session_factory() as session:
        shadow = session.query(TaskShadow).one()
        assert shadow.ticktick_task_id == "task-1"
        assert shadow.normalized_title == "给导师发邮件"
        assert shadow.semantic_type == "explicit_time"
        assert shadow.raw_nl_time is None
        assert shadow.due_at == datetime.fromisoformat("2026-03-28T15:00:00-07:00").replace(tzinfo=None)


@pytest.mark.asyncio
async def test_execute_action_reuses_cached_tasks_across_batch_updates() -> None:
    from ticktick_telegram_assistant.services.task_command_service import (
        TaskCommandExecutionCache,
        TaskCommandService,
    )

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="task-1",
            projectId="telegram-inbox",
            title="和家里打电话",
            status=0,
        ),
        TickTickTask(
            id="task-2",
            projectId="telegram-inbox",
            title="处理ds2019的事情",
            status=0,
        ),
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)
    cache = TaskCommandExecutionCache()

    complete_reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(action_type="complete_task", payload={"title": "和家里打电话"}),
        execution_cache=cache,
    )
    update_reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            payload={
                "match_title": "处理ds2019的事情",
                "due_at": "2026-03-29T22:00:00-07:00",
            },
        ),
        execution_cache=cache,
    )

    assert "和家里打电话" in complete_reply
    assert "处理ds2019的事情" in update_reply
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
        {
            "method": "complete_task",
            "access_token": "access-token",
            "project_id": "telegram-inbox",
            "task_id": "task-1",
        },
        {
            "method": "update_task",
            "access_token": "access-token",
            "task_id": "task-2",
            "patch": {
                "id": "task-2",
                "projectId": "telegram-inbox",
                "dueDate": "2026-03-29T22:00:00-0700",
                "timeZone": "America/Los_Angeles",
            },
        },
    ]


@pytest.mark.asyncio
async def test_execute_action_queues_retryable_write_for_later_replay() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(id="task-1", projectId="telegram-inbox", title="和家里打电话", status=0),
    ]
    client.fail_on_methods["complete_task"] = make_ticktick_500_error(
        "/open/v1/project/telegram-inbox/task/task-1/complete"
    )
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(action_type="complete_task", payload={"title": "和家里打电话"}),
        source_text="和家里打电话已完成",
        retry_dedupe_key="retry:99:msg-1:complete",
    )

    assert "排队" in reply
    with session_factory() as session:
        reminders = session.query(ReminderEvent).all()
        assert len(reminders) == 1
        reminder = reminders[0]
        assert reminder.event_type == "ticktick_write_retry"
        assert reminder.status == "pending"
        assert reminder.dedupe_key == "retry:99:msg-1:complete"
        assert reminder.payload_json["action"]["action_type"] == "complete_task"
        assert reminder.payload_json["source_text"] == "和家里打电话已完成"


@pytest.mark.asyncio
async def test_execute_action_creates_task_with_repeat_priority_tags_and_checklist() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="create_task",
            payload={
                "title": "每周同步",
                "semantic_type": "explicit_time",
                "due_at": "2026-03-30T10:00:00-07:00",
                "repeat_rule": "FREQ=WEEKLY;BYDAY=MO",
                "priority": 3,
                "tags": ["work", "weekly"],
                "subtasks": ["准备议程", "发纪要"],
            },
        ),
    )

    assert "重复规则" in reply
    assert "已尝试同步标签" in reply
    assert client.calls == [
        {"method": "list_projects", "access_token": "access-token"},
        {
            "method": "create_task",
            "access_token": "access-token",
            "task": {
                "title": "每周同步",
                "projectId": "telegram-inbox",
                "content": "",
                "desc": "",
                "dueDate": "2026-03-30T10:00:00-0700",
                "timeZone": "America/Los_Angeles",
                "reminders": [],
                "repeatFlag": "FREQ=WEEKLY;BYDAY=MO",
                "priority": 3,
                "items": [
                    {"title": "准备议程"},
                    {"title": "发纪要"},
                ],
                "tags": ["work", "weekly"],
            },
        },
    ]


@pytest.mark.asyncio
async def test_execute_action_normalizes_repeat_phrase_and_time_span() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="create_task",
            payload={
                "title": "周五和导师开会",
                "semantic_type": "explicit_time",
                "start_at": "2026-04-03T17:00:00-07:00",
                "duration_minutes": 90,
                "repeat_rule": "每周五",
            },
        ),
    )

    assert "17:00-18:30" in reply
    assert "FREQ=WEEKLY;BYDAY=FR" in reply
    assert client.calls == [
        {"method": "list_projects", "access_token": "access-token"},
        {
            "method": "create_task",
            "access_token": "access-token",
            "task": {
                "title": "周五和导师开会",
                "projectId": "telegram-inbox",
                "content": "",
                "desc": "",
                "startDate": "2026-04-03T17:00:00-0700",
                "dueDate": "2026-04-03T18:30:00-0700",
                "timeZone": "America/Los_Angeles",
                "reminders": [],
                "repeatFlag": "FREQ=WEEKLY;BYDAY=FR",
                "items": [],
            },
        },
    ]

    with session_factory() as session:
        shadow = session.query(TaskShadow).one()
        assert shadow.start_at == datetime.fromisoformat("2026-04-03T17:00:00-07:00").replace(tzinfo=None)
        assert shadow.end_at == datetime.fromisoformat("2026-04-03T18:30:00-07:00").replace(tzinfo=None)


@pytest.mark.asyncio
async def test_execute_action_creates_windowed_task_with_shadow_metadata() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="create_task",
            payload={
                "title": "把周报框架补完",
                "semantic_type": "windowed",
                "raw_nl_time": "下周",
                "window_start": "2026-03-30T00:00:00-07:00",
                "window_end": "2026-04-05T23:59:00-07:00",
            },
        ),
    )

    assert "把周报框架补完" in reply
    assert "下周" in reply
    assert client.calls == [
        {"method": "list_projects", "access_token": "access-token"},
        {
            "method": "create_task",
            "access_token": "access-token",
            "task": {
                "title": "把周报框架补完",
                "projectId": "telegram-inbox",
                "desc": "时间窗口：下周",
                "content": "",
                "reminders": [],
                "items": [],
            },
        },
    ]

    with session_factory() as session:
        shadow = session.query(TaskShadow).one()
        assert shadow.semantic_type == "windowed"
        assert shadow.window_start == datetime.fromisoformat("2026-03-30T00:00:00-07:00").replace(tzinfo=None)
        assert shadow.window_end == datetime.fromisoformat("2026-04-05T23:59:00-07:00").replace(tzinfo=None)
        assert shadow.raw_nl_time == "下周"


@pytest.mark.asyncio
async def test_execute_action_completes_unique_matching_task() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(id="task-1", projectId="telegram-inbox", title="给导师发邮件", status=0),
        TickTickTask(id="task-2", projectId="telegram-inbox", title="别的事", status=0),
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="complete_task",
            payload={"title": "给导师发邮件"},
        ),
    )

    assert reply == "好，这条我帮你勾完成了：给导师发邮件"
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
        {
            "method": "complete_task",
            "access_token": "access-token",
            "project_id": "telegram-inbox",
            "task_id": "task-1",
        },
    ]


@pytest.mark.asyncio
async def test_execute_action_asks_for_confirmation_when_multiple_tasks_match_completion() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(id="task-1", projectId="telegram-inbox", title="给导师发邮件", status=0),
        TickTickTask(id="task-2", projectId="fun", title="给导师发邮件", status=0),
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="complete_task",
            payload={"title": "给导师发邮件"},
        ),
    )

    assert "不止一条" in reply
    assert "给导师发邮件" in reply
    assert client.calls == [{"method": "list_tasks", "access_token": "access-token", "since": None}]


@pytest.mark.asyncio
async def test_execute_action_updates_unique_task_time_and_description() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="task-1",
            projectId="telegram-inbox",
            title="weekly sync",
            desc="原说明",
            status=0,
        )
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            payload={
                "match_title": "weekly sync",
                "due_at": "2026-03-28T15:00:00-07:00",
                "description": "记得带 agenda",
                "description_mode": "append",
            },
        ),
    )

    assert "weekly sync" in reply
    assert "15:00" in reply
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
        {
            "method": "update_task",
            "access_token": "access-token",
            "task_id": "task-1",
            "patch": {
                "id": "task-1",
                "projectId": "telegram-inbox",
                "desc": "原说明\n记得带 agenda",
                "dueDate": "2026-03-28T15:00:00-0700",
                "timeZone": "America/Los_Angeles",
            },
        },
    ]


@pytest.mark.asyncio
async def test_execute_action_updates_repeat_rule_tags_and_checklist() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="task-1",
            projectId="telegram-inbox",
            title="weekly sync",
            desc="原说明",
            status=0,
        )
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            payload={
                "match_title": "weekly sync",
                "repeat_rule": "FREQ=WEEKLY;BYDAY=MO",
                "priority": 2,
                "tags": ["team"],
                "subtasks": ["准备议程"],
            },
        ),
    )

    assert "重复规则" in reply
    assert "已尝试同步标签" in reply
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
            {
                "method": "update_task",
                "access_token": "access-token",
                "task_id": "task-1",
                "patch": {
                    "id": "task-1",
                    "projectId": "telegram-inbox",
                    "repeatFlag": "FREQ=WEEKLY;BYDAY=MO",
                    "priority": 2,
                    "items": [
                        {"title": "准备议程"},
                    ],
                    "tags": ["team"],
                },
            },
        ]


@pytest.mark.asyncio
async def test_execute_action_can_clear_repeat_tags_and_checklist() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="task-1",
                semantic_type="explicit_time",
                normalized_title="weekly sync",
                tags_json=["old"],
            )
        )
        session.commit()

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="task-1",
            projectId="telegram-inbox",
            title="weekly sync",
            desc="原说明",
            status=0,
        )
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            payload={
                "match_title": "weekly sync",
                "repeat_rule": "取消循环",
                "tags": [],
                "subtasks": [],
            },
        ),
    )

    assert "重复规则已清除" in reply
    assert "标签已清空" in reply
    assert "清单已清空" in reply
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
        {
            "method": "update_task",
            "access_token": "access-token",
            "task_id": "task-1",
            "patch": {
                "id": "task-1",
                "projectId": "telegram-inbox",
                "repeatFlag": "",
                "items": [],
                "tags": [],
            },
        },
    ]

    with session_factory() as session:
        shadow = session.query(TaskShadow).one()
        assert shadow.tags_json == []


@pytest.mark.asyncio
async def test_execute_action_updates_windowed_task_and_replaces_old_window_metadata() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="task-1",
                semantic_type="windowed",
                normalized_title="把周报框架补完",
                window_start=datetime.fromisoformat("2026-03-30T00:00:00-07:00").replace(tzinfo=None),
                window_end=datetime.fromisoformat("2026-04-05T23:59:00-07:00").replace(tzinfo=None),
                raw_nl_time="上周",
                due_at=datetime.fromisoformat("2026-03-28T09:00:00-07:00").replace(tzinfo=None),
                start_at=datetime.fromisoformat("2026-03-28T08:00:00-07:00").replace(tzinfo=None),
                end_at=datetime.fromisoformat("2026-03-28T10:00:00-07:00").replace(tzinfo=None),
                timezone_mode="floating",
            )
        )
        session.commit()

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="task-1",
            projectId="telegram-inbox",
            title="把周报框架补完",
            desc="先整理材料\n时间窗口：上周",
            status=0,
        )
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            payload={
                "match_title": "把周报框架补完",
                "semantic_type": "windowed",
                "window_start": "2026-04-06T00:00:00-07:00",
                "window_end": "2026-04-12T23:59:00-07:00",
                "raw_nl_time": "下周",
            },
        ),
    )

    assert "说明已更新" in reply
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
        {
            "method": "update_task",
            "access_token": "access-token",
            "task_id": "task-1",
            "patch": {
                "id": "task-1",
                "projectId": "telegram-inbox",
                "desc": "先整理材料\n时间窗口：下周",
            },
        },
    ]

    with session_factory() as session:
        shadow = session.query(TaskShadow).one()
        assert shadow.semantic_type == "windowed"
        assert shadow.window_start == datetime.fromisoformat("2026-04-06T00:00:00-07:00").replace(tzinfo=None)
        assert shadow.window_end == datetime.fromisoformat("2026-04-12T23:59:00-07:00").replace(tzinfo=None)
        assert shadow.raw_nl_time == "下周"
        assert shadow.due_at is None
        assert shadow.start_at is None
        assert shadow.end_at is None
        assert shadow.timezone_mode == "floating"


@pytest.mark.asyncio
async def test_execute_action_updates_task_time_span_and_repeat_phrase() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="task-1",
            projectId="telegram-inbox",
            title="weekly sync",
            desc="原说明",
            status=0,
        )
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            payload={
                "match_title": "weekly sync",
                "start_at": "2026-03-28T15:00:00-07:00",
                "end_at": "2026-03-28T16:30:00-07:00",
                "repeat_rule": "工作日",
            },
        ),
    )

    assert "15:00-16:30" in reply
    assert "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" in reply
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
        {
            "method": "update_task",
            "access_token": "access-token",
            "task_id": "task-1",
            "patch": {
                "id": "task-1",
                "projectId": "telegram-inbox",
                "startDate": "2026-03-28T15:00:00-0700",
                "dueDate": "2026-03-28T16:30:00-0700",
                "timeZone": "America/Los_Angeles",
                "repeatFlag": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
            },
        },
    ]


@pytest.mark.asyncio
async def test_execute_action_reclassifies_memo_shadow_when_scheduling_it() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="memo-1",
                semantic_type="memo",
                normalized_title="整理发票",
                raw_nl_time="周末",
            )
        )
        session.commit()

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="memo-1",
            projectId="telegram-inbox",
            title="整理发票",
            desc="周末看看",
            status=0,
        )
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            target_task_id="memo-1",
            payload={
                "match_title": "整理发票",
                "semantic_type": "explicit_time",
                "due_at": "2026-03-30T10:00:00-07:00",
            },
        ),
    )

    assert "10:00" in reply
    with session_factory() as session:
        shadow = session.query(TaskShadow).one()
        assert shadow.semantic_type == "explicit_time"
        assert shadow.due_at == datetime.fromisoformat("2026-03-30T10:00:00-07:00").replace(tzinfo=None)
        assert shadow.window_start is None
        assert shadow.window_end is None


@pytest.mark.asyncio
async def test_execute_action_asks_for_confirmation_when_multiple_tasks_match_update() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(id="task-1", projectId="telegram-inbox", title="weekly sync", status=0),
        TickTickTask(id="task-2", projectId="fun", title="weekly sync", status=0),
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            payload={"match_title": "weekly sync", "due_at": "2026-03-28T15:00:00-07:00"},
        ),
    )

    assert "不止一条" in reply
    assert "weekly sync" in reply
    assert client.calls == [{"method": "list_tasks", "access_token": "access-token", "since": None}]


@pytest.mark.asyncio
async def test_execute_action_updates_target_task_id_without_ambiguity() -> None:
    from ticktick_telegram_assistant.services.task_command_service import TaskCommandService

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

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(id="task-1", projectId="telegram-inbox", title="给导师A发邮件", status=0),
        TickTickTask(id="task-2", projectId="fun", title="给导师A发邮件", status=0),
    ]
    service = TaskCommandService(session_factory=session_factory, ticktick_client=client)

    reply = await service.execute_action(
        telegram_user_id="99",
        action=PlannedAction(
            action_type="update_task",
            target_task_id="task-2",
            payload={
                "match_title": "给导师A发邮件",
                "description": "记得带附件",
                "description_mode": "append",
            },
        ),
    )

    assert "给导师A发邮件" in reply
    assert client.calls == [
        {"method": "list_tasks", "access_token": "access-token", "since": None},
        {
            "method": "update_task",
            "access_token": "access-token",
            "task_id": "task-2",
            "patch": {
                "id": "task-2",
                "projectId": "fun",
                "desc": "记得带附件",
            },
        },
    ]
