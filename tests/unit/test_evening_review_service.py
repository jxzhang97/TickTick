from ticktick_telegram_assistant.services.evening_review_service import EveningReviewService


def test_evening_review_parses_batch_completion_reply() -> None:
    service = EveningReviewService()
    result = service.parse_reply(
        reply_text="前两个做完了，第三个改到周四下午",
        candidate_titles=["写周报", "回导师邮件", "整理实验记录"],
    )
    assert result.completed_indices == [0, 1]
    assert result.rescheduled_indices == [2]
