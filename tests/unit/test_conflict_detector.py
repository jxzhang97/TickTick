from ticktick_telegram_assistant.services.conflict_detector import ConflictDetector


def test_conflict_detector_flags_time_overlap() -> None:
    detector = ConflictDetector()
    assert detector.has_conflict(
        "2026-03-17T15:00:00-06:00",
        "2026-03-17T15:30:00-06:00",
    )


def test_conflict_detector_flags_span_overlap() -> None:
    detector = ConflictDetector()
    assert detector.has_conflict(
        "2026-03-17T15:00:00-06:00",
        "2026-03-17T15:30:00-06:00",
        end_iso="2026-03-17T16:00:00-06:00",
        other_end_iso="2026-03-17T16:30:00-06:00",
    )


def test_conflict_detector_ignores_non_overlapping_spans() -> None:
    detector = ConflictDetector()
    assert not detector.has_conflict(
        "2026-03-17T15:00:00-06:00",
        "2026-03-17T17:00:00-06:00",
        end_iso="2026-03-17T16:00:00-06:00",
        other_end_iso="2026-03-17T18:00:00-06:00",
    )
