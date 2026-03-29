from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickProject, TickTickTask


class FakeTickTickClient:
    def __init__(self) -> None:
        self.projects: list[TickTickProject] = []
        self.tasks: list[TickTickTask] = []
        self.project_error: Exception | None = None
        self.task_error: Exception | None = None

    async def list_projects(self, *, access_token: str) -> list[TickTickProject]:
        if self.project_error is not None:
            raise self.project_error
        return list(self.projects)

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        if self.task_error is not None:
            raise self.task_error
        return list(self.tasks)


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def make_user(session: Session) -> User:
    user = User(
        telegram_user_id="99",
        display_name="Jiaxin",
        current_timezone="America/Los_Angeles",
        ticktick_access_token="access-token",
    )
    session.add(user)
    session.flush()
    return user


def make_ticktick_500_error(path: str = "/open/v1/project") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://api.ticktick.com{path}")
    response = httpx.Response(500, request=request)
    return httpx.HTTPStatusError("temporary failure", request=request, response=response)


@pytest.mark.asyncio
async def test_snapshot_service_returns_cached_tasks_on_retryable_error() -> None:
    from ticktick_telegram_assistant.services.ticktick_snapshot_service import TickTickSnapshotService

    session_factory = make_session_factory()
    with session_factory() as session:
        user = make_user(session)
        session.commit()

    client = FakeTickTickClient()
    client.tasks = [
        TickTickTask(
            id="task-1",
            projectId="telegram-inbox",
            title="周三前交报告",
            dueDate="2026-04-01T18:00:00-0700",
            status=0,
        )
    ]
    service = TickTickSnapshotService(session_factory=session_factory, ticktick_client=client)

    first = await service.list_tasks_for_user(user=user, now=datetime.fromisoformat("2026-03-29T08:00:00-07:00"))
    assert first.is_stale is False
    assert [task.title for task in first.items] == ["周三前交报告"]

    client.task_error = make_ticktick_500_error("/open/v1/project")
    second = await service.list_tasks_for_user(user=user, now=datetime.fromisoformat("2026-03-29T08:05:00-07:00"))
    assert second.is_stale is True
    assert second.snapshot_synced_at is not None
    assert [task.title for task in second.items] == ["周三前交报告"]
