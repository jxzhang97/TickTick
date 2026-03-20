from ticktick_telegram_assistant.services.context_builder import ContextBuilder


def test_regression_splits_contextual_edit_sample() -> None:
    sample = "改到明天下午\n再补一句说明\n放到 fun 那个 list"
    lines = ContextBuilder().split_lines(sample)
    assert lines == ["改到明天下午", "再补一句说明", "放到 fun 那个 list"]
