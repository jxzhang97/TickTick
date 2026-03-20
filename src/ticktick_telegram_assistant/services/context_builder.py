from ticktick_telegram_assistant.domain.schemas import ConversationContext


class ContextBuilder:
    def build(self, text: str) -> ConversationContext:
        return ConversationContext(user_text=text)

    def split_lines(self, text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip()]

