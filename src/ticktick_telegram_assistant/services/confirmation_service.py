from ticktick_telegram_assistant.domain.schemas import ConfirmationRequest


class ConfirmationService:
    def request_confirmation(self, question: str, options: list[str] | None = None) -> ConfirmationRequest:
        return ConfirmationRequest(question=question, options=options or [])

