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
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickTask


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, *, chat_id: int, text: str) -> dict:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return {"ok": True}


class FakeTickTickClient:
    def __init__(
        self,
        tasks: list[TickTickTask],
        *,
        fail_on_call_numbers: set[int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.tasks = tasks
        self.calls: list[dict] = []
        self.fail_on_call_numbers = fail_on_call_numbers or set()
        self.error = error or RuntimeError("ticktick list_tasks failed")

    async def list_tasks(self, *, access_token: str, since=None) -> list[TickTickTask]:
        self.calls.append({"method": "list_tasks", "access_token": access_token, "since": since})
        if len(self.calls) in self.fail_on_call_numbers:
            raise self.error
        return list(self.tasks)


class FakeTaskCommandService:
    def __init__(self, reply_text: str = "ok") -> None:
        self.reply_text = reply_text
        self.calls: list[dict] = []

    async def execute_action(
        self,
        *,
        telegram_user_id: str,
        action,
        now=None,
        execution_cache=None,
        source_text: str | None = None,
        retry_dedupe_key: str | None = None,
        allow_retry_queue: bool = True,
    ) -> str:
        self.calls.append(
            {
                "telegram_user_id": telegram_user_id,
                "action": action,
                "allow_retry_queue": allow_retry_queue,
                "source_text": source_text,
            }
        )
        return self.reply_text


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
async def test_reminder_worker_uses_today_brief_structure_for_morning_brief() -> None:
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
                title="上午先发邮件",
                dueDate="2026-03-29T11:00:00.000-0700",
                priority=5,
                status=0,
            ),
            TickTickTask(
                id="t2",
                projectId="telegram-inbox",
                title="周三前交报告",
                dueDate="2026-04-01T18:00:00.000-0700",
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

    await worker.run_once(now=datetime.fromisoformat("2026-03-29T08:00:10-07:00"))

    assert len(telegram_client.sent_messages) == 1
    message = telegram_client.sent_messages[0]["text"]
    assert "2026-03-29 周日" in message
    assert "今天有明确时间的任务" in message
    assert "未完成的事情提醒（需要跟进的截止项）" in message
    assert "接下来 7 天的明确安排和截止提醒" in message
    assert "未来 7 天里适合找空完成的事" in message
    assert "04-01 周三" in message


@pytest.mark.asyncio
async def test_reminder_worker_backfills_morning_brief_after_missed_trigger() -> None:
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
                title="补发晨报测试",
                dueDate="2026-03-29T15:00:00.000-0700",
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

    await worker.run_once(now=datetime.fromisoformat("2026-03-29T08:17:10-07:00"))

    assert len(telegram_client.sent_messages) == 1
    assert "补发晨报测试" in telegram_client.sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_reminder_worker_queues_and_backfills_morning_brief_when_ticktick_fetch_recovers() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

    request = httpx.Request("GET", "https://api.ticktick.com/open/v1/project")
    response = httpx.Response(500, request=request)
    ticktick_error = httpx.HTTPStatusError("temporary failure", request=request, response=response)

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
        ],
        fail_on_call_numbers={1},
        error=ticktick_error,
    )
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    first_run_at = datetime.fromisoformat("2026-03-27T08:00:10-07:00")
    try:
        await worker.run_once(now=first_run_at)
    except httpx.HTTPStatusError as exc:
        pytest.fail(f"morning brief fetch failure should be queued locally, not raised: {exc}")

    assert telegram_client.sent_messages == []
    with session_factory() as session:
        reminders = session.query(ReminderEvent).all()
        assert len(reminders) == 1
        reminder = reminders[0]
        assert reminder.event_type == "morning_brief"
        assert reminder.status == "pending"
        assert reminder.dedupe_key == "morning_brief:2026-03-27"

    ticktick_client.fail_on_call_numbers.clear()
    await worker.run_once(now=datetime.fromisoformat("2026-03-27T08:05:10-07:00"))

    assert len(telegram_client.sent_messages) == 1
    assert "早呀" in telegram_client.sent_messages[0]["text"]
    with session_factory() as session:
        reminders = session.query(ReminderEvent).all()
        assert len(reminders) == 1
        reminder = reminders[0]
        assert reminder.event_type == "morning_brief"
        assert reminder.status == "sent"
        assert reminder.sent_at is not None


@pytest.mark.asyncio
async def test_reminder_worker_replays_pending_ticktick_write_retry() -> None:
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
                event_type="ticktick_write_retry",
                scheduled_at=datetime.fromisoformat("2026-03-29T21:00:00-07:00"),
                status="pending",
                dedupe_key="retry:99:msg-1:complete",
                payload_json={
                    "action": PlannedAction(
                        action_type="complete_task",
                        payload={"title": "和家里打电话"},
                    ).model_dump(mode="json"),
                    "source_text": "和家里打电话已完成",
                },
            )
        )
        session.commit()

    telegram_client = FakeTelegramClient()
    task_command_service = FakeTaskCommandService("好，这条我帮你勾完成了：和家里打电话")
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=FakeTickTickClient([]),
        telegram_client=telegram_client,
        task_command_service=task_command_service,
    )

    await worker.run_once(now=datetime.fromisoformat("2026-03-29T21:05:00-07:00"))

    assert len(task_command_service.calls) == 1
    assert task_command_service.calls[0]["allow_retry_queue"] is False
    assert len(telegram_client.sent_messages) == 1
    assert "已经补上了" in telegram_client.sent_messages[0]["text"]
    with session_factory() as session:
        reminder = session.query(ReminderEvent).one()
        assert reminder.status == "sent"


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
async def test_reminder_worker_sends_just_missed_prestart_catch_up_reminder() -> None:
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
                dueDate="2026-03-27T14:00:00.000-0700",
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

    await worker.run_once(now=datetime.fromisoformat("2026-03-27T14:00:45-07:00"))

    assert len(telegram_client.sent_messages) == 1
    message = telegram_client.sent_messages[0]["text"]
    assert "刚刚错过" in message
    assert "周五讨论" in message
    with session_factory() as session:
        reminder = session.query(ReminderEvent).one()
        assert reminder.event_type == "prestart_reminder"
        assert reminder.status == "sent"


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
async def test_reminder_worker_backfills_evening_review_after_midnight_restart() -> None:
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

    await worker.run_once(now=datetime.fromisoformat("2026-03-28T00:10:00-07:00"))

    assert len(telegram_client.sent_messages) == 1
    assert "今天这些事哪些已经做完了" in telegram_client.sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_reminder_worker_queues_and_backfills_evening_review_when_ticktick_fetch_recovers() -> None:
    from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker

    request = httpx.Request("GET", "https://api.ticktick.com/open/v1/project")
    response = httpx.Response(500, request=request)
    ticktick_error = httpx.HTTPStatusError("temporary failure", request=request, response=response)

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
        ],
        fail_on_call_numbers={1},
        error=ticktick_error,
    )
    telegram_client = FakeTelegramClient()
    worker = ReminderWorker(
        session_factory=session_factory,
        ticktick_client=ticktick_client,
        telegram_client=telegram_client,
    )

    try:
        await worker.run_once(now=datetime.fromisoformat("2026-03-27T23:30:10-07:00"))
    except httpx.HTTPStatusError as exc:
        pytest.fail(f"evening review fetch failure should be queued locally, not raised: {exc}")

    assert telegram_client.sent_messages == []
    with session_factory() as session:
        reminders = session.query(ReminderEvent).all()
        assert len(reminders) == 1
        reminder = reminders[0]
        assert reminder.event_type == "evening_review"
        assert reminder.status == "pending"
        assert reminder.dedupe_key == "evening_review:2026-03-27"

    ticktick_client.fail_on_call_numbers.clear()
    await worker.run_once(now=datetime.fromisoformat("2026-03-28T00:10:00-07:00"))

    assert len(telegram_client.sent_messages) == 1
    assert "今天这些事哪些已经做完了" in telegram_client.sent_messages[0]["text"]
    with session_factory() as session:
        reminders = session.query(ReminderEvent).all()
        assert len(reminders) == 1
        reminder = reminders[0]
        assert reminder.event_type == "evening_review"
        assert reminder.status == "sent"
        assert reminder.sent_at is not None


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


@pytest.mark.asyncio
async def test_reminder_worker_backfills_weekly_memo_cleanup_after_missed_trigger() -> None:
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
                ticktick_task_id="memo-1",
                semantic_type="memo",
                normalized_title="整理发票",
                raw_nl_time="周末",
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

    await worker.run_once(now=datetime.fromisoformat("2026-03-29T20:15:00-07:00"))

    assert len(telegram_client.sent_messages) == 1
    assert "周末收尾一下" in telegram_client.sent_messages[0]["text"]
