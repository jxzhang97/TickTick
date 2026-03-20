from ticktick_telegram_assistant.services.reminder_service import ReminderService


def test_explicit_time_task_gets_t_minus_five_event() -> None:
    events = ReminderService().build_events(
        task={"id": "t1", "semantic_type": "explicit_time", "due_at": "2026-03-17T15:00:00-06:00"}
    )
    assert any(event["event_type"] == "prestart_reminder" for event in events)
