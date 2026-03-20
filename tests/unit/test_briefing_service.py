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
