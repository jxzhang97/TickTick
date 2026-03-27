from ticktick_telegram_assistant.services.reminder_service import ReminderService
from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker
from ticktick_telegram_assistant.workers.scheduler import Scheduler


def test_explicit_time_task_gets_t_minus_five_event() -> None:
    events = ReminderService().build_events(
        task={"id": "t1", "semantic_type": "explicit_time", "due_at": "2026-03-17T15:00:00-06:00"}
    )
    assert any(event["event_type"] == "prestart_reminder" for event in events)


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
