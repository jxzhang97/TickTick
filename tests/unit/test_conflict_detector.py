from ticktick_telegram_assistant.services.conflict_detector import ConflictDetector


def test_conflict_detector_flags_time_overlap() -> None:
    detector = ConflictDetector()
    assert detector.has_conflict(
        "2026-03-17T15:00:00-06:00",
        "2026-03-17T15:30:00-06:00",
    )
