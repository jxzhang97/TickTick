from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import (
    TickTickProject,
    TickTickProjectData,
    TickTickTask,
)


class FakeTickTickClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
        return [
            TickTickTask(
                id="t1",
                projectId="p1",
                title="11 点前发邮件",
                desc="回导师",
                dueDate="2026-03-27T18:00:00+0000",
                status=0,
            ),
            TickTickTask(
                id="t2",
                projectId="p1",
                title="下午发周报给产品组",
                desc="别忘了补结论",
                dueDate="2026-03-30T18:00:00+0000",
                status=0,
            ),
            TickTickTask(
                id="t3",
                projectId="p1",
                title="一周内整理实验记录",
                dueDate="2026-04-02T18:00:00+0000",
                status=0,
            ),
            TickTickTask(
                id="t4",
                projectId="p1",
                title="更远的 ddl",
                dueDate="2026-04-10T18:00:00+0000",
                status=0,
            ),
        ]


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@pytest.mark.asyncio
async def test_build_today_brief_renders_today_items_in_user_timezone() -> None:
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

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
                ticktick_task_id="shadow-1",
                semantic_type="windowed",
                normalized_title="整理实验记录",
                raw_nl_time="这两周",
                window_start=datetime.fromisoformat("2026-03-27T00:00:00-07:00"),
                window_end=datetime.fromisoformat("2026-04-02T23:59:00-07:00"),
            )
        )
        session.commit()

    service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient(),
    )

    message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
    )

    assert "今天最重要的几件" in message
    assert "今天有明确时间的安排" in message
    assert "未来 7 天的 ddl" in message
    assert "这几天要推进的时间窗口任务" in message
    assert "周五" in message
    assert "11:00" in message
    assert "回导师" in message
    assert "发周报给产品组" in message
    assert "别忘了补结论" in message
    assert "一周内整理实验记录" in message
    assert "更远的 ddl" not in message


@pytest.mark.asyncio
async def test_build_today_brief_renders_upcoming_items_even_without_today_deadlines() -> None:
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    class FutureOnlyClient(FakeTickTickClient):
        async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
            self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
            return [
                TickTickTask(
                    id="t1",
                    projectId="p1",
                    title="三天后开会",
                    dueDate="2026-03-30T18:00:00+0000",
                    status=0,
                )
            ]

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

    service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FutureOnlyClient(),
    )

    message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
    )

    assert "今天最重要的几件" in message
    assert "三天后开会" in message
