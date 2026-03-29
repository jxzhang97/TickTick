from ticktick_telegram_assistant.services.evening_review_service import EveningReviewService


def test_evening_review_parses_batch_completion_reply() -> None:
    service = EveningReviewService()
    result = service.parse_reply(
        reply_text="前两个都做完了，第三个和第四个顺延到周四下午",
        candidate_titles=["写周报", "回导师邮件", "整理实验记录", "补实验数据"],
    )
    assert result.completed_indices == [0, 1]
    assert result.rescheduled_indices == [2, 3]


def test_evening_review_parses_more_natural_batch_reply() -> None:
    service = EveningReviewService()
    result = service.parse_reply(
        reply_text="除了第三个，前面两个和最后一个都完成了",
        candidate_titles=["写周报", "回导师邮件", "整理实验记录", "整理附件"],
    )
    assert result.completed_indices == [0, 1, 3]
    assert result.rescheduled_indices == []
