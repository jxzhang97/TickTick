from datetime import datetime

from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


def test_render_weekday_returns_chinese_weekday() -> None:
    renderer = MessageRenderer()
    rendered = renderer.render_weekday(datetime.fromisoformat("2026-03-17T09:00:00"))
    assert rendered == "周二"
