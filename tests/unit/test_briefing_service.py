from datetime import datetime

from ticktick_telegram_assistant.services.briefing_service import BriefingService


def test_briefing_includes_weekday_and_focus_items() -> None:
    service = BriefingService()
    rendered = service.render_morning_brief(
        current_time=datetime.fromisoformat("2026-03-17T09:00:00-07:00"),
        today_timed_items=[
            {
                "title": "交周报",
                "time_label": "09:00",
                "description": "发给产品组",
            }
        ],
        today_date_only_items=[],
        active_windowed_items=[],
        overdue_items=[],
        upcoming_explicit_items=[],
        upcoming_windowed_items=[],
        memo_items=[],
    )
    assert "2026-03-17 周二" in rendered
    assert "交周报" in rendered
    assert "发给产品组" in rendered


def test_briefing_keeps_fixed_sections_and_compresses_long_lists() -> None:
    service = BriefingService()
    rendered = service.render_morning_brief(
        current_time=datetime.fromisoformat("2026-03-17T09:00:00-07:00"),
        today_timed_items=[
            {"title": f"任务{i}", "time_label": f"0{i}:00", "description": None}
            for i in range(1, 8)
        ],
        today_date_only_items=[],
        active_windowed_items=[],
        overdue_items=[],
        upcoming_explicit_items=[],
        upcoming_windowed_items=[],
        memo_items=[],
    )

    assert "今天有明确时间的任务" in rendered
    assert "今天要留意的日期任务" in rendered
    assert "未完成的事情提醒（需要跟进的截止项）" in rendered
    assert "接下来 7 天的明确安排和截止提醒" in rendered
    assert "今天没有只写日期但没写具体时间的任务" in rendered
    assert "其余 2 件" in rendered


def test_briefing_keeps_fixed_sections_when_everything_is_empty() -> None:
    service = BriefingService()
    rendered = service.render_morning_brief(
        current_time=datetime.fromisoformat("2026-03-17T09:00:00-07:00"),
        today_timed_items=[],
        today_date_only_items=[],
        active_windowed_items=[],
        overdue_items=[],
        upcoming_explicit_items=[],
        upcoming_windowed_items=[],
        memo_items=[],
    )

    assert "今天有明确时间的任务" in rendered
    assert "今天要留意的日期任务" in rendered
    assert "这段时间可以找空推进的事" in rendered
    assert "顺手记着的小备忘（暂未安排具体时间，记下以便后续安排）" in rendered
    assert "今天没有明确到钟点的任务" in rendered
