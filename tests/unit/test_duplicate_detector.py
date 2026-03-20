from ticktick_telegram_assistant.services.duplicate_detector import DuplicateDetector


def test_duplicate_detector_flags_similar_task() -> None:
    detector = DuplicateDetector()
    assert detector.is_probable_duplicate("写周报", "周报写完")
