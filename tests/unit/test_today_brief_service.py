from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickTask


class FakeTickTickClient:
    def __init__(self, tasks: list[TickTickTask], *, error: Exception | None = None) -> None:
        self.tasks = tasks
        self.error = error
        self.calls: list[dict] = []

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
        if self.error is not None:
            raise self.error
        return self.tasks


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


def make_ticktick_500_error(path: str = "/open/v1/project") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://api.ticktick.com{path}")
    response = httpx.Response(500, request=request)
    return httpx.HTTPStatusError("temporary failure", request=request, response=response)


@pytest.mark.asyncio
async def test_build_today_brief_renders_explicit_date_anchor_and_separates_today_buckets() -> None:
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    tasks = [
        TickTickTask(
            id="overdue-1",
            projectId="p1",
            title="昨天没交的报告",
            desc="先补结论",
            dueDate="2026-03-26T18:00:00-0700",
            status=0,
        ),
        TickTickTask(
            id="today-timed-1",
            projectId="p1",
            title="上午先发邮件",
            desc="发给导师",
            startDate="2026-03-27T09:00:00-0700",
            dueDate="2026-03-27T09:30:00-0700",
            status=0,
        ),
        TickTickTask(
            id="today-date-1",
            projectId="p1",
            title="今天补报销",
            desc="只要今天做掉",
            isAllDay=True,
            dueDate="2026-03-27T18:00:00-0700",
            status=0,
        ),
        TickTickTask(
            id="future-scheduled-1",
            projectId="p1",
            title="周二下午评审会",
            desc="产品组",
            startDate="2026-03-31T14:00:00-0700",
            dueDate="2026-03-31T15:30:00-0700",
            status=0,
        ),
        TickTickTask(
            id="future-ddl-1",
            projectId="p1",
            title="周三前交报告",
            desc="别忘了结论",
            dueDate="2026-04-01T18:00:00-0700",
            status=0,
        ),
        TickTickTask(
            id="far-future-1",
            projectId="p1",
            title="更远的 ddl",
            dueDate="2026-04-10T18:00:00-0700",
            status=0,
        ),
    ]

    session_factory = make_session_factory()
    with session_factory() as session:
        user = add_user(session)
        session.add(
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="shadow-window-1",
                semantic_type="windowed",
                normalized_title="整理实验记录",
                raw_nl_time="这两周",
                window_start=datetime.fromisoformat("2026-03-27T00:00:00-07:00"),
                window_end=datetime.fromisoformat("2026-04-02T23:59:00-07:00"),
            )
        )
        session.add(
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="shadow-memo-1",
                semantic_type="memo",
                normalized_title="回导师邮件",
                raw_nl_time="下班后",
            )
        )
        session.commit()

    service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient(tasks),
    )

    message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
    )

    assert "2026-03-27" in message
    assert "周五" in message
    assert "今天有明确时间的任务" in message
    assert "今天要留意的日期任务" in message
    assert "未完成的事情提醒（需要跟进的截止项）" in message
    assert "接下来 7 天的明确安排和截止提醒" in message
    assert "这段时间可以找空推进的事" in message
    assert "顺手记着的小备忘（暂未安排具体时间，记下以便后续安排）" in message
    assert "发给导师" in message
    assert "只要今天做掉" in message
    assert "先补结论" in message
    assert "别忘了结论" in message
    assert "这两周" in message
    assert "更远的 ddl" not in message

    today_timed_section = message.split("今天有明确时间的任务", 1)[1].split("今天要留意的日期任务", 1)[0]
    today_date_only_section = message.split("今天要留意的日期任务", 1)[1].split("这段时间可以找空推进的事", 1)[0]
    window_section = message.split("这段时间可以找空推进的事", 1)[1].split("未完成的事情提醒（需要跟进的截止项）", 1)[0]
    overdue_section = message.split("未完成的事情提醒（需要跟进的截止项）", 1)[1].split("接下来 7 天的明确安排和截止提醒", 1)[0]
    future_section = message.split("接下来 7 天的明确安排和截止提醒", 1)[1].split("未来 7 天里适合找空完成的事", 1)[0]
    backlog_section = message.split("顺手记着的小备忘（暂未安排具体时间，记下以便后续安排）", 1)[1]

    assert "上午先发邮件" in today_timed_section
    assert "今天补报销" in today_date_only_section
    assert "昨天没交的报告" in overdue_section
    assert "周二下午评审会" in future_section
    assert "周三前交报告" in future_section
    assert "整理实验记录" in window_section
    assert "回导师邮件" in backlog_section
    assert "今天补报销" not in today_timed_section
    assert "周二下午评审会" not in today_timed_section
    assert "周三前交报告" not in today_timed_section


@pytest.mark.asyncio
async def test_build_today_brief_keeps_future_items_out_of_today_buckets() -> None:
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    tasks = [
        TickTickTask(
            id="future-1",
            projectId="p1",
            title="三天后开会",
            desc="产品组",
            dueDate="2026-03-30T18:00:00-0700",
            status=0,
        )
    ]

    session_factory = make_session_factory()
    with session_factory() as session:
        add_user(session)
        session.commit()

    service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient(tasks),
    )

    message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
    )

    assert "接下来 7 天的明确安排和截止提醒" in message
    future_section = message.split("接下来 7 天的明确安排和截止提醒", 1)[1].split("未来 7 天里适合找空完成的事", 1)[0]
    assert "三天后开会" in future_section

    today_timed_section = message.split("今天有明确时间的任务", 1)[1].split("今天要留意的日期任务", 1)[0]
    today_date_only_section = message.split("今天要留意的日期任务", 1)[1].split("这段时间可以找空推进的事", 1)[0]
    assert "三天后开会" not in today_timed_section
    assert "三天后开会" not in today_date_only_section


@pytest.mark.asyncio
async def test_build_today_brief_renders_memo_backlog_and_window_sections_even_without_task_lists() -> None:
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    session_factory = make_session_factory()
    with session_factory() as session:
        user = add_user(session)
        session.add(
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="shadow-window-1",
                semantic_type="windowed",
                normalized_title="整理实验记录",
                raw_nl_time="这两周",
                window_start=datetime.fromisoformat("2026-03-27T00:00:00-07:00"),
                window_end=datetime.fromisoformat("2026-04-02T23:59:00-07:00"),
            )
        )
        session.add(
            TaskShadow(
                user_id=user.id,
                ticktick_task_id="shadow-memo-1",
                semantic_type="memo",
                normalized_title="回导师邮件",
                raw_nl_time="下班后",
            )
        )
        session.commit()

    service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient([]),
    )

    message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
    )

    assert "这段时间可以找空推进的事" in message
    assert "顺手记着的小备忘（暂未安排具体时间，记下以便后续安排）" in message
    assert "整理实验记录" in message
    assert "这两周" in message
    assert "回导师邮件" in message


@pytest.mark.asyncio
async def test_build_today_brief_uses_cached_tasks_when_ticktick_temporarily_fails() -> None:
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    tasks = [
        TickTickTask(
            id="future-ddl-1",
            projectId="p1",
            title="周三前交报告",
            desc="别忘了结论",
            dueDate="2026-04-01T18:00:00-0700",
            status=0,
        ),
    ]

    session_factory = make_session_factory()
    with session_factory() as session:
        add_user(session)
        session.commit()

    client = FakeTickTickClient(tasks)
    service = TodayBriefService(session_factory=session_factory, ticktick_client=client)

    first_message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-29T08:00:00-07:00"),
    )
    assert "周三前交报告" in first_message

    client.error = make_ticktick_500_error()
    second_message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-29T08:05:00-07:00"),
    )
    assert "周三前交报告" in second_message
    assert "本地快照" in second_message


@pytest.mark.asyncio
async def test_build_today_brief_uses_cached_snapshot_when_ticktick_temporarily_fails() -> None:
    from ticktick_telegram_assistant.services.ticktick_snapshot_service import TickTickSnapshotService
    from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService

    session_factory = make_session_factory()
    with session_factory() as session:
        user = add_user(session)
        session.commit()

    cached_tasks = [
        TickTickTask(
            id="cached-1",
            projectId="p1",
            title="缓存里的今天任务",
            dueDate="2026-03-29T14:00:00-0700",
            status=0,
        )
    ]
    snapshot_service = TickTickSnapshotService(session_factory=session_factory)
    snapshot_service.save_tasks(
        user_id=user.id,
        tasks=cached_tasks,
        fetched_at=datetime.fromisoformat("2026-03-29T07:50:00-07:00"),
    )

    service = TodayBriefService(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient([], error=make_ticktick_500_error()),
        snapshot_service=snapshot_service,
    )

    message = await service.build_today_brief(
        telegram_user_id="99",
        now=datetime.fromisoformat("2026-03-29T08:00:00-07:00"),
    )

    assert "缓存里的今天任务" in message
    assert "本地快照" in message
