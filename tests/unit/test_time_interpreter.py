from datetime import datetime

from ticktick_telegram_assistant.services.time_interpreter import TimeInterpreter


def test_interpreter_marks_windowed_tasks() -> None:
    result = TimeInterpreter().parse(
        "下周把周报框架补完",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "windowed"
    assert result.window_start.isoformat().startswith("2026-03-23")


def test_interpreter_marks_explicit_time_tasks() -> None:
    result = TimeInterpreter().parse(
        "明天下午3点开会",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "explicit_time"
    assert result.due_at.isoformat().startswith("2026-03-17T15:00")
