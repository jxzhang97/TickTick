from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
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
    assert "早呀，今天先抓重点" in message
    assert "周五" in message
    assert "周一" in message
    assert "下周" in message


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
