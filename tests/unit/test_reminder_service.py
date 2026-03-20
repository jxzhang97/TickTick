from ticktick_telegram_assistant.services.reminder_service import ReminderService


def test_snooze_keeps_original_due_time() -> None:
    event = ReminderService().build_snooze_event(
        task={"id": "t1", "due_at": "2026-03-17T15:00:00-06:00"},
        request_text="1小时后再提醒我",
    )
    assert event["scheduled_at"].startswith("2026-03-17T16:00")
    assert event["task_due_at"] == "2026-03-17T15:00:00-06:00"
