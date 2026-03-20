from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.user import User


def test_models_expose_expected_tablenames() -> None:
    assert User.__tablename__ == "users"
    assert ReminderEvent.__tablename__ == "reminder_events"
