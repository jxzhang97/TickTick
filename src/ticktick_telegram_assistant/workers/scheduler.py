from ticktick_telegram_assistant.services.reminder_service import ReminderService


class Scheduler:
    def __init__(self, reminder_service: ReminderService | None = None) -> None:
        self._reminder_service = reminder_service or ReminderService()

    def schedule_for_task(self, task: dict) -> list[dict]:
        return self._reminder_service.build_events(task)

