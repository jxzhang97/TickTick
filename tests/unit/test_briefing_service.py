from ticktick_telegram_assistant.services.briefing_service import BriefingService


def test_briefing_includes_weekday_and_focus_items() -> None:
    service = BriefingService()
    rendered = service.render_morning_brief(
        top_items=[
            {
                "title": "交周报",
                "when": "2026-03-17 09:00",
                "weekday": "周二",
                "description": "发给产品组",
            }
        ],
        scheduled_items=[],
        ddl_items=[],
        windowed_items=[],
    )
    assert "周二" in rendered
    assert "交周报" in rendered
    assert "发给产品组" in rendered


def test_briefing_keeps_fixed_sections_and_compresses_long_lists() -> None:
    service = BriefingService()
    rendered = service.render_morning_brief(
        top_items=[],
        scheduled_items=[
            {"title": f"任务{i}", "when": f"0{i}:00", "weekday": "周二", "description": None}
            for i in range(1, 8)
        ],
        ddl_items=[],
        windowed_items=[],
    )

    assert "今天最重要的几件：" in rendered
    assert "今天有明确时间的安排：" in rendered
    assert "未来 7 天的 ddl：" in rendered
    assert "这几天要推进的时间窗口任务：" in rendered
    assert "暂时没有" in rendered
    assert "其余 2 件" in rendered


def test_briefing_keeps_fixed_sections_when_everything_is_empty() -> None:
    service = BriefingService()
    rendered = service.render_morning_brief(
        top_items=[],
        scheduled_items=[],
        ddl_items=[],
        windowed_items=[],
    )

    assert "今天最重要的几件：" in rendered
    assert "今天有明确时间的安排：" in rendered
    assert "未来 7 天的 ddl：" in rendered
    assert "这几天要推进的时间窗口任务：" in rendered
    assert rendered.count("暂时没有") >= 3
