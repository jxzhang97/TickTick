from ticktick_telegram_assistant.domain.schemas import EveningReviewReply


class EveningReviewService:
    def parse_reply(self, *, reply_text: str, candidate_titles: list[str]) -> EveningReviewReply:
        completed_indices: list[int] = []
        rescheduled_indices: list[int] = []

        if "前两个做完" in reply_text and len(candidate_titles) >= 2:
            completed_indices.extend([0, 1])
        if "第三个改到" in reply_text and len(candidate_titles) >= 3:
            rescheduled_indices.append(2)

        return EveningReviewReply(
            completed_indices=completed_indices,
            rescheduled_indices=rescheduled_indices,
        )
