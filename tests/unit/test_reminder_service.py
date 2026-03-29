from ticktick_telegram_assistant.services.reminder_service import ReminderService


def test_snooze_uses_reminder_time_when_available() -> None:
    event = ReminderService().build_snooze_event(
        task={
            "id": "t1",
            "due_at": "2026-03-17T15:00:00-06:00",
            "scheduled_at": "2026-03-17T14:55:00-06:00",
        },
        request_text="1小时后再提醒我",
    )
    assert event["scheduled_at"].startswith("2026-03-17T15:55")
    assert event["task_due_at"] == "2026-03-17T14:55:00-06:00"


def test_snooze_understands_tonight_phrase() -> None:
    event = ReminderService().build_snooze_event(
        task={
            "id": "t1",
            "scheduled_at": "2026-03-17T18:10:00-06:00",
        },
        request_text="今晚8点再提醒",
    )
    assert event["scheduled_at"].startswith("2026-03-17T20:00")
    assert event["task_due_at"] == "2026-03-17T18:10:00-06:00"
