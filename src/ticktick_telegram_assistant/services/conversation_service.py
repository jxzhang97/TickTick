from pydantic import BaseModel, ConfigDict, Field

from ticktick_telegram_assistant.domain.schemas import PlannedConversation, TelegramReply
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner
from ticktick_telegram_assistant.services.context_builder import ContextBuilder
from ticktick_telegram_assistant.services.evening_review_service import EveningReviewService


class TelegramUser(BaseModel):
    id: int


class TelegramChat(BaseModel):
    id: int
    type: str


class TelegramMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    from_: TelegramUser | None = Field(default=None, alias="from")
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

    async def handle_update(self, update: TelegramUpdate) -> list[TelegramReply]:
        if update.message is None or update.message.text is None:
            return []
        if self._is_evening_review_reply(update.message.text):
            return await self._handle_evening_review_reply(
                chat_id=update.message.chat.id,
                text=update.message.text,
            )
        context = self._context_builder.build(update.message.text)
        planned = await self._planner.plan(context)
        reply_text = planned.assistant_reply or self._fallback_reply(update.message.text)
        return [TelegramReply(chat_id=update.message.chat.id, text=reply_text)]

    async def update_user_timezone(self, *, user_id: int, timezone_name: str, source: str) -> None:
        self._user_timezones[user_id] = {"timezone_name": timezone_name, "source": source}

    def _is_evening_review_reply(self, text: str) -> bool:
        return "做完" in text or "改到" in text

    async def _handle_evening_review_reply(self, *, chat_id: int, text: str) -> list[TelegramReply]:
        parsed = self._evening_review_service.parse_reply(reply_text=text, candidate_titles=[])
        if parsed.completed_indices or parsed.rescheduled_indices:
            reply_text = "收到，我先记下你刚才说的完成和改时间意图。等 TickTick 执行链路接上后，我会直接替你处理。"
        else:
            reply_text = "我收到了。你这句像是在回晚间回顾，但现在还没稳定定位到具体任务，先别急，我会继续补这条链路。"
        return [TelegramReply(chat_id=chat_id, text=reply_text)]

    def _fallback_reply(self, text: str) -> str:
        lowered = text.lower()
        if "今天" in text and any(keyword in text for keyword in ("安排", "日程", "ddl", "deadline")):
            return "我已经收到了你要看今天安排的意思，但你的 TickTick 读取链路还没接通，所以现在还拉不出今天的任务。"
        if any(keyword in text for keyword in ("改到", "改成", "挪到", "推到", "提前", "延后", "补一句", "放到")):
            return "我看懂你是在改已有任务，但现在还没连上 TickTick，先不冒险帮你写入，免得改错。"
        if any(keyword in text for keyword in ("做完", "完成", "勾掉", "勾选")):
            return "我收到了你想勾完成，不过 TickTick 执行链路还没接上，所以这一步我先不乱动。"
        if "ticktick" in lowered:
            return "我在，基础服务已经起来了。下一步是把 TickTick 授权和真实读写链路接上，这样我才能真的替你查和改任务。"
        return "我收到你的话了。现在 Telegram 收发已经打通，但 TickTick 的真实执行链路还在补，所以我先不瞎写入。"


class NoopConversationService(ConversationService):
    def __init__(self) -> None:
        super().__init__(context_builder=ContextBuilder(), planner=OpenAIPlanner(client=None))

    async def handle_update(self, update: TelegramUpdate) -> list[TelegramReply]:
        return []
