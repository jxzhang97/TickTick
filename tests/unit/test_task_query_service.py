from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.schemas import PlannedQueryIntent
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickTask


class FakeTickTickClient:
    def __init__(self, tasks: list[TickTickTask]) -> None:
        self.tasks = tasks
        self.calls: list[dict] = []

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
        return list(self.tasks)


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def add_user(session: Session) -> User:
    user = User(
        telegram_user_id="99",
        display_name="Jiaxin",
        current_timezone="America/Los_Angeles",
        ticktick_access_token="access-token",
    )
    session.add(user)
    session.flush()
    return user


@pytest.mark.asyncio
async def test_build_query_reply_recent_includes_overdue_upcoming_and_active_windowed() -> None:
    from ticktick_telegram_assistant.services.task_query_service import TaskQueryService
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    tasks = [
        TickTickTask(
            id="overdue-1",
            projectId="p1",
            title="补交材料",
            desc="已经拖了两天",
            dueDate="2026-03-27T18:00:00-0700",
            status=0,
        ),
        TickTickTask(
            id="today-1",
            projectId="p1",
            title="去健身房",
            startDate="2026-03-29T14:00:00-0700",
            dueDate="2026-03-29T15:00:00-0700",
            status=0,
        ),
        TickTickTask(
            id="future-1",
            projectId="p1",
            title="听 talk",
            dueDate="2026-04-01T16:00:00-0700",
            status=0,
        ),
    ]
    session_factory = make_session_factory()
    with session_factory() as session:
        user = add_user(session)
        session.add(
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="window-1",
                semantic_type="windowed",
                normalized_title="整理实验记录",
                raw_nl_time="这两周",
                window_start=datetime.fromisoformat("2026-03-28T00:00:00-07:00"),
                window_end=datetime.fromisoformat("2026-04-05T23:59:00-07:00"),
            )
        )
        session.add(
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="memo-1",
                semantic_type="memo",
                normalized_title="记一下想看的文章",
            )
        )
        session.commit()

    today_brief_service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient(tasks),
    )
    service = TaskQueryService(
        session_factory=session_factory,
        ticktick_client=today_brief_service._ticktick_client,
        today_brief_service=today_brief_service,
    )

    message = await service.build_query_reply(
        telegram_user_id="99",
        query=PlannedQueryIntent(
            query_kind="schedule_query",
            query_text="我最近有什么事",
            time_scope="recent",
        ),
        now=datetime.fromisoformat("2026-03-29T09:00:00-07:00"),
    )

    assert "最近这段时间" in message
    assert "补交材料" in message
    assert "去健身房" in message
    assert "听 talk" in message
    assert "整理实验记录" in message
    assert "记一下想看的文章" not in message


@pytest.mark.asyncio
async def test_build_query_reply_overdue_only_lists_overdue_items() -> None:
    from ticktick_telegram_assistant.services.task_query_service import TaskQueryService
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    tasks = [
        TickTickTask(
            id="overdue-1",
            projectId="p1",
            title="补交材料",
            dueDate="2026-03-27T18:00:00-0700",
            status=0,
        ),
        TickTickTask(
            id="future-1",
            projectId="p1",
            title="听 talk",
            dueDate="2026-04-01T16:00:00-0700",
            status=0,
        ),
    ]
    session_factory = make_session_factory()
    with session_factory() as session:
        add_user(session)
        session.commit()

    today_brief_service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient(tasks),
    )
    service = TaskQueryService(
        session_factory=session_factory,
        ticktick_client=today_brief_service._ticktick_client,
        today_brief_service=today_brief_service,
    )

    message = await service.build_query_reply(
        telegram_user_id="99",
        query=PlannedQueryIntent(
            query_kind="schedule_query",
            query_text="我手上还挂着什么",
            time_scope="overdue",
        ),
        now=datetime.fromisoformat("2026-03-29T09:00:00-07:00"),
    )

    assert "截止项" in message
    assert "补交材料" in message
    assert "听 talk" not in message


@pytest.mark.asyncio
async def test_build_query_reply_this_week_filters_to_current_week() -> None:
    from ticktick_telegram_assistant.services.task_query_service import TaskQueryService
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    tasks = [
        TickTickTask(
            id="this-week-1",
            projectId="p1",
            title="周日开会",
            dueDate="2026-03-29T14:00:00-0700",
            status=0,
        ),
        TickTickTask(
            id="next-week-1",
            projectId="p1",
            title="下周二评审",
            dueDate="2026-03-31T14:00:00-0700",
            status=0,
        ),
    ]
    session_factory = make_session_factory()
    with session_factory() as session:
        add_user(session)
        session.commit()

    today_brief_service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient(tasks),
    )
    service = TaskQueryService(
        session_factory=session_factory,
        ticktick_client=today_brief_service._ticktick_client,
        today_brief_service=today_brief_service,
    )

    message = await service.build_query_reply(
        telegram_user_id="99",
        query=PlannedQueryIntent(
            query_kind="schedule_query",
            query_text="这周有什么安排",
            time_scope="this_week",
        ),
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
    )

    assert "本周" in message
    assert "周日开会" in message
    assert "下周二评审" not in message
