from datetime import datetime

from ticktick_telegram_assistant.services.time_interpreter import TimeInterpreter


def test_interpreter_marks_windowed_tasks() -> None:
    result = TimeInterpreter().parse(
        "下周把周报框架补完",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "windowed"
    assert result.window_start.isoformat().startswith("2026-03-23")
    assert result.window_end.isoformat().startswith("2026-03-29")


def test_interpreter_marks_two_week_windows() -> None:
    result = TimeInterpreter().parse(
        "这两周把实验记录整理好",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "windowed"
    assert result.window_start.isoformat().startswith("2026-03-16")
    assert result.window_end.isoformat().startswith("2026-03-29")


def test_interpreter_marks_month_end_windows() -> None:
    result = TimeInterpreter().parse(
        "月底前整理完",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "windowed"
    assert result.window_end.isoformat().startswith("2026-03-31")


def test_interpreter_treats_tomorrow_afternoon_as_window_not_fake_exact_time() -> None:
    result = TimeInterpreter().parse(
        "明天下午把材料再看一遍",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "windowed"
    assert result.window_start.isoformat().startswith("2026-03-17T12:00")
    assert result.window_end.isoformat().startswith("2026-03-17T18:00")


def test_interpreter_marks_explicit_time_tasks() -> None:
    result = TimeInterpreter().parse(
        "明天下午3点开会",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "explicit_time"
    assert result.due_at.isoformat().startswith("2026-03-17T15:00")


def test_interpreter_marks_tonight_exact_times() -> None:
    result = TimeInterpreter().parse(
        "今晚8点再提醒我",
        now=datetime.fromisoformat("2026-03-16T09:00:00-06:00"),
    )
    assert result.semantic_type == "explicit_time"
    assert result.due_at.isoformat().startswith("2026-03-16T20:00")


def test_interpreter_marks_month_day_deadlines_as_explicit_time() -> None:
    result = TimeInterpreter().parse(
        "改到4月3号",
        now=datetime.fromisoformat("2026-03-29T09:00:00-07:00"),
    )
    assert result.semantic_type == "explicit_time"
    assert result.due_at.isoformat().startswith("2026-04-03T23:59")
