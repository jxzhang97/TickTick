from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickTask


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, *, chat_id: int, text: str) -> dict:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return {"ok": True}


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


@pytest.mark.asyncio
async def test_reminder_worker_sends_morning_brief_once_per_day() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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
                ticktick_task_id="window-1",
                semantic_type="windowed",
                normalized_title="把周报框架补完",
                raw_nl_time="下周",
                window_start=datetime.fromisoformat("2026-03-30T00:00:00"),
                window_end=datetime.fromisoformat("2026-04-05T23:59:00"),
            )
        )
        session.commit()

    ticktick_client = FakeTickTickClient(
        [
            TickTickTask(
                id="t1",
                projectId="telegram-inbox",
                title="周五讨论",
                desc="记得带 notes",
                dueDate="2026-03-27T21:00:00.000+0000",
                status=0,
            ),
            TickTickTask(
                id="t2",
                projectId="telegram-inbox",
                title="周一交周报",
                dueDate="2026-03-30T19:00:00.000+0000",
                status=0,
            ),
        ]
    )
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    now = datetime.fromisoformat("2026-03-27T08:00:10-07:00")
    await worker.run_once(now=now)
    await worker.run_once(now=now)

    assert len(telegram_client.sent_messages) == 1
    message = telegram_client.sent_messages[0]["text"]
    assert "早呀" in message
    assert "周五" in message
    assert "周一" in message
    assert "下周" in message


@pytest.mark.asyncio
async def test_reminder_worker_morning_brief_shows_time_range_for_span_task() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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

    ticktick_client = FakeTickTickClient(
        [
            TickTickTask(
                id="t-span",
                projectId="telegram-inbox",
                title="上午评审会",
                startDate="2026-03-27T14:00:00.000-0700",
                dueDate="2026-03-27T15:30:00.000-0700",
                status=0,
            )
        ]
    )
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    await worker.run_once(now=datetime.fromisoformat("2026-03-27T08:00:10-07:00"))

    assert len(telegram_client.sent_messages) == 1
    assert "14:00-15:30" in telegram_client.sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_reminder_worker_sends_prestart_reminder_once() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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

    ticktick_client = FakeTickTickClient(
        [
            TickTickTask(
                id="t1",
                projectId="telegram-inbox",
                title="周五讨论",
                desc="记得带 notes",
                dueDate="2026-03-27T21:00:00.000+0000",
                status=0,
            )
        ]
    )
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    now = datetime.fromisoformat("2026-03-27T13:55:10-07:00")
    await worker.run_once(now=now)
    await worker.run_once(now=now)

    assert len(telegram_client.sent_messages) == 1
    assert "还有 5 分钟" in telegram_client.sent_messages[0]["text"]
    assert "周五讨论" in telegram_client.sent_messages[0]["text"]
    with session_factory() as session:
        reminder = session.query(ReminderEvent).one()
        assert reminder.event_type == "prestart_reminder"
        assert reminder.payload_json["task_id"] == "t1"
        assert reminder.payload_json["title"] == "周五讨论"
        assert reminder.payload_json["task_due_at"] == "2026-03-27T13:55:00-07:00"


@pytest.mark.asyncio
async def test_reminder_worker_anchors_span_tasks_on_start_time() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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

    ticktick_client = FakeTickTickClient(
        [
            TickTickTask(
                id="t-span",
                projectId="telegram-inbox",
                title="上午评审会",
                dueDate="2026-03-27T18:00:00.000-0700",
                startDate="2026-03-27T14:00:00.000-0700",
                status=0,
            )
        ]
    )
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    await worker.run_once(now=datetime.fromisoformat("2026-03-27T13:55:10-07:00"))

    assert len(telegram_client.sent_messages) == 1
    message = telegram_client.sent_messages[0]["text"]
    assert "14:00" in message
    assert "18:00" not in message


@pytest.mark.asyncio
async def test_reminder_worker_sends_evening_review_once() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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

    ticktick_client = FakeTickTickClient(
        [
            TickTickTask(
                id="t1",
                projectId="telegram-inbox",
                title="周五讨论",
                dueDate="2026-03-27T21:00:00.000+0000",
                status=0,
            )
        ]
    )
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    now = datetime.fromisoformat("2026-03-27T23:30:10-07:00")
    await worker.run_once(now=now)
    await worker.run_once(now=now)

    assert len(telegram_client.sent_messages) == 1
    message = telegram_client.sent_messages[0]["text"]
    assert "今天这些事哪些已经做完了" in message
    assert "周五讨论" in message
    with session_factory() as session:
        reminder = session.query(ReminderEvent).one()
        assert reminder.event_type == "evening_review"
        assert reminder.payload_json["candidate_tasks"] == [
            {"task_id": "t1", "title": "周五讨论"}
        ]


@pytest.mark.asyncio
async def test_reminder_worker_sends_windowed_progress_pings() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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
                ticktick_task_id="window-1",
                semantic_type="windowed",
                normalized_title="把周报框架补完",
                raw_nl_time="下周",
                window_start=datetime.fromisoformat("2026-03-30T00:00:00"),
                window_end=datetime.fromisoformat("2026-04-05T23:59:00"),
            )
        )
        session.commit()

    ticktick_client = FakeTickTickClient([])
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    await worker.run_once(now=datetime.fromisoformat("2026-03-30T00:00:10-07:00"))
    await worker.run_once(now=datetime.fromisoformat("2026-04-03T12:00:10-07:00"))
    await worker.run_once(now=datetime.fromisoformat("2026-04-04T00:00:10-07:00"))

    assert len(telegram_client.sent_messages) == 3
    assert "下周" in telegram_client.sent_messages[0]["text"]
    assert "把周报框架补完" in telegram_client.sent_messages[0]["text"]
    assert "把周报框架补完" in telegram_client.sent_messages[1]["text"]
    assert "把周报框架补完" in telegram_client.sent_messages[2]["text"]


@pytest.mark.asyncio
async def test_reminder_worker_sends_due_pending_snooze_event() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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
                event_type="snoozed_reminder",
                scheduled_at=datetime.fromisoformat("2026-03-29T16:00:00-07:00"),
                dedupe_key="snooze:task-1:2026-03-29T16:00:00-07:00",
                status="pending",
                payload_json={"text": "今晚 8 点再提醒：给导师A发邮件"},
            )
        )
        session.commit()

    ticktick_client = FakeTickTickClient([])
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    await worker.run_once(now=datetime.fromisoformat("2026-03-29T16:00:10-07:00"))

    assert telegram_client.sent_messages == [
        {"chat_id": 99, "text": "今晚 8 点再提醒：给导师A发邮件"}
    ]
    with session_factory() as session:
        reminder = session.query(ReminderEvent).one()
        assert reminder.status == "sent"
        assert reminder.sent_at is not None


@pytest.mark.asyncio
async def test_reminder_worker_sends_weekly_memo_cleanup_for_memo_shadows() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

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
        session.add_all(
            [
                TaskShadow(
                    user_id=user.id,
                    ticktick_task_id="memo-1",
                    semantic_type="memo",
                    normalized_title="整理发票",
                    raw_nl_time="周末",
                ),
                TaskShadow(
                    user_id=user.id,
                    ticktick_task_id="memo-2",
                    semantic_type="memo",
                    normalized_title="回邮件",
                    raw_nl_time="下班后",
                ),
            ]
        )
        session.commit()

    ticktick_client = FakeTickTickClient([])
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    now = datetime.fromisoformat("2026-03-29T17:00:10-07:00")
    await worker.run_once(now=now)
    await worker.run_once(now=now)

    assert len(telegram_client.sent_messages) == 1
    message = telegram_client.sent_messages[0]["text"]
    assert "周末收尾一下" in message
    assert "整理发票" in message
    assert "回邮件" in message
    with session_factory() as session:
        reminder = session.query(ReminderEvent).one()
        assert reminder.event_type == "memo_cleanup"
        assert reminder.payload_json["candidate_tasks"] == [
            {"task_id": "memo-2", "title": "回邮件", "description": "下班后"},
            {"task_id": "memo-1", "title": "整理发票", "description": "周末"},
        ]
