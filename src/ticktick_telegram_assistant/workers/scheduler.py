from __future__ import annotations

import asyncio

from ticktick_telegram_assistant.services.reminder_service import ReminderService
from ticktick_telegram_assistant.workers.reminder_worker import ReminderWorker


class Scheduler:
    def __init__(
        self,
        reminder_service: ReminderService | None = None,
        reminder_worker: ReminderWorker | None = None,
    ) -> None:
        self._reminder_service = reminder_service or ReminderService()
        self._reminder_worker = reminder_worker or ReminderWorker()

    def schedule_for_task(self, task: dict) -> list[dict]:
        return self._reminder_service.build_events(task)

    def run_tick(self) -> None:
        asyncio.run(self._reminder_worker.run_once())
