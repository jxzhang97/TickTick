from ticktick_telegram_assistant.services.reminder_service import ReminderService
from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker
from ticktick_telegram_assistant.workers.scheduler import Scheduler


def test_explicit_time_task_gets_t_minus_five_event() -> None:
    events = ReminderService().build_events(
        task={"id": "t1", "semantic_type": "explicit_time", "due_at": "2026-03-17T15:00:00-06:00"}
    )
    assert any(event["event_type"] == "prestart_reminder" for event in events)


def test_snooze_event_uses_reminder_time_when_available() -> None:
    event = ReminderService().build_snooze_event(
        task={
            "id": "t1",
            "due_at": "2026-03-17T15:00:00-06:00",
            "scheduled_at": "2026-03-17T14:55:00-06:00",
        },
        request_text="今晚8点再提醒",
    )
    assert event["scheduled_at"].startswith("2026-03-17T20:00")
    assert event["task_due_at"] == "2026-03-17T14:55:00-06:00"


class CountingReminderWorker(ReminderWorker):
    def __init__(self) -> None:
        self.runs = 0

    async def run_once(self) -> None:
        self.runs += 1


def test_scheduler_runs_reminder_worker_once() -> None:
    worker = CountingReminderWorker()
    scheduler = Scheduler(reminder_worker=worker)

    scheduler.run_tick()

    assert worker.runs == 1
