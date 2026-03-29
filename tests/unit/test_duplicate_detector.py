from ticktick_telegram_assistant.services.duplicate_detector import DuplicateDetector


def test_duplicate_detector_flags_similar_task() -> None:
    detector = DuplicateDetector()
    assert detector.is_probable_duplicate("写周报", "周报写完")


def test_duplicate_detector_ignores_different_named_targets() -> None:
    detector = DuplicateDetector()
    assert not detector.is_probable_duplicate("给导师A发邮件", "给导师B发邮件")


def test_duplicate_detector_normalizes_spacing_and_punctuation() -> None:
    detector = DuplicateDetector()
    assert detector.is_probable_duplicate("给导师A发邮件", "给导师A 发邮件")
