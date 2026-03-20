from pydantic import BaseModel, Field

from ticktick_telegram_assistant.domain.schemas import PlannedConversation
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner
from ticktick_telegram_assistant.services.context_builder import ContextBuilder


class TelegramUser(BaseModel):
    id: int


class TelegramChat(BaseModel):
    id: int
    type: str


class TelegramMessage(BaseModel):
    message_id: int
    from_: TelegramUser | None = None
    chat: TelegramChat
    text: str | None = None
    location: dict | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None


class ConversationService:
    def __init__(
        self,
        context_builder: ContextBuilder | None = None,
        planner: OpenAIPlanner | None = None,
    ) -> None:
        self._context_builder = context_builder or ContextBuilder()
        self._planner = planner or OpenAIPlanner()

    async def handle_update(self, update: TelegramUpdate) -> None:
        if update.message is None or update.message.text is None:
            return None
        context = self._context_builder.build(update.message.text)
        await self._planner.plan(context)
        return None


class NoopConversationService(ConversationService):
    def __init__(self) -> None:
        super().__init__(context_builder=ContextBuilder(), planner=OpenAIPlanner(client=None))

    async def handle_update(self, update: TelegramUpdate) -> None:
        return None
