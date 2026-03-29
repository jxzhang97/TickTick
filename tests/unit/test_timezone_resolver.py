from ticktick_telegram_assistant.services.timezone_resolver import TimezoneResolver


def test_resolve_from_text_understands_common_timezone_phrases() -> None:
    resolver = TimezoneResolver()

    assert resolver.resolve_from_text("太平洋时间") == "America/Los_Angeles"
    assert resolver.resolve_from_text("Pacific time") == "America/Los_Angeles"
    assert resolver.resolve_from_text("Eastern Daylight Time") == "America/New_York"
    assert resolver.resolve_from_text("UTC+8") == "Etc/GMT-8"
    assert resolver.resolve_from_text("America/Chicago") == "America/Chicago"


def test_resolve_from_text_rejects_invalid_zone_names() -> None:
    resolver = TimezoneResolver()

    assert resolver.resolve_from_text("Mars/Phobos") is None
