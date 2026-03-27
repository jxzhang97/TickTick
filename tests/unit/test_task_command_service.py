from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
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

    async def list_projects(self, *, access_token: str) -> list[TickTickProject]:
        self.calls.append({"method": "list_projects", "access_token": access_token})
        return [
            TickTickProject(id="telegram-inbox", name="Telegram Inbox", kind="TASK"),
            TickTickProject(id="fun", name="fun", kind="TASK"),
        ]

    async def create_task(self, *, access_token: str, task) -> TickTickTask:
        payload = task.model_dump(exclude_none=True)
        self.calls.append({"method": "create_task", "access_token": access_token, "task": payload})
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
