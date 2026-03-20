from pydantic import BaseModel, Field

from ticktick_telegram_assistant.domain.schemas import PlannedConversation
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner
from ticktick_telegram_assistant.services.context_builder import ContextBuilder
from ticktick_telegram_assistant.services.evening_review_service import EveningReviewService


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
        evening_review_service: EveningReviewService | None = None,
    ) -> None:
        self._context_builder = context_builder or ContextBuilder()
        self._planner = planner or OpenAIPlanner()
        self._evening_review_service = evening_review_service or EveningReviewService()
        self._user_timezones: dict[int, dict[str, str]] = {}

    async def handle_update(self, update: TelegramUpdate) -> None:
        if update.message is None or update.message.text is None:
            return None
        if self._is_evening_review_reply(update.message.text):
            await self._handle_evening_review_reply(update.message.text)
            return None
        context = self._context_builder.build(update.message.text)
        await self._planner.plan(context)
        return None

    async def update_user_timezone(self, *, user_id: int, timezone_name: str, source: str) -> None:
        self._user_timezones[user_id] = {"timezone_name": timezone_name, "source": source}

    def _is_evening_review_reply(self, text: str) -> bool:
        return "做完" in text or "改到" in text

    async def _handle_evening_review_reply(self, text: str) -> None:
        self._evening_review_service.parse_reply(reply_text=text, candidate_titles=[])
        return None


class NoopConversationService(ConversationService):
    def __init__(self) -> None:
        super().__init__(context_builder=ContextBuilder(), planner=OpenAIPlanner(client=None))

    async def handle_update(self, update: TelegramUpdate) -> None:
        return None
