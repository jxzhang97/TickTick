from __future__ import annotations

from datetime import datetime, timezone
import re

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.domain.schemas import PlannedAction, PlannedConversation, TelegramReply
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner
from ticktick_telegram_assistant.repositories.reminders import ReminderRepository
from ticktick_telegram_assistant.repositories.users import UserRepository
from ticktick_telegram_assistant.services.conflict_detector import ConflictDetector
from ticktick_telegram_assistant.services.context_builder import ContextBuilder
from ticktick_telegram_assistant.services.duplicate_detector import DuplicateDetector
from ticktick_telegram_assistant.services.evening_review_service import EveningReviewService
from ticktick_telegram_assistant.services.memory_service import MemoryService
from ticktick_telegram_assistant.services.reminder_service import ReminderService
from ticktick_telegram_assistant.services.timezone_resolver import TimezoneResolver


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
        ticktick_oauth_service=None,
        today_brief_service=None,
        task_command_service=None,
        session_factory: sessionmaker[Session] | None = None,
        reminder_service: ReminderService | None = None,
        timezone_resolver: TimezoneResolver | None = None,
        memory_service: MemoryService | None = None,
        ticktick_client=None,
        duplicate_detector: DuplicateDetector | None = None,
        conflict_detector: ConflictDetector | None = None,
    ) -> None:
        self._context_builder = context_builder or ContextBuilder()
        self._planner = planner or OpenAIPlanner()
        self._evening_review_service = evening_review_service or EveningReviewService()
        self._ticktick_oauth_service = ticktick_oauth_service
        self._today_brief_service = today_brief_service
        self._task_command_service = task_command_service
        self._session_factory = session_factory
        self._reminder_service = reminder_service or ReminderService()
        self._timezone_resolver = timezone_resolver or TimezoneResolver()
        self._memory_service = memory_service
        self._ticktick_client = ticktick_client
        self._duplicate_detector = duplicate_detector or DuplicateDetector()
        self._conflict_detector = conflict_detector or ConflictDetector()
        self._user_timezones: dict[int, dict[str, str]] = {}
        self._active_task_contexts: dict[str, dict[str, str]] = {}

    async def handle_update(self, update: TelegramUpdate) -> list[TelegramReply]:
        if update.message is None:
            return []
        location_reply = await self._maybe_handle_location_update(update)
        if location_reply is not None:
            return [location_reply]
        if update.message.text is None:
            return []
        self._record_turn_summary(
            telegram_user_id=self._telegram_user_id(update),
            text=update.message.text,
        )
        timezone_text_reply = await self._maybe_handle_timezone_text_update(update)
        if timezone_text_reply is not None:
            return [timezone_text_reply]
        snooze_reply = await self._maybe_handle_snooze_request(update)
        if snooze_reply is not None:
            return [snooze_reply]
        confirmation_reply = await self._maybe_handle_pending_confirmation(update)
        if confirmation_reply is not None:
            return [confirmation_reply]
        batch_replies = await self._maybe_handle_multiline_batch(update)
        if batch_replies is not None:
            return batch_replies
        if self._is_evening_review_reply(update.message.text):
            return await self._handle_evening_review_reply(
                telegram_user_id=self._telegram_user_id(update),
                chat_id=update.message.chat.id,
                text=update.message.text,
                current_timezone=self._current_timezone_for_update(update),
            )
        ticktick_reply = await self._maybe_build_ticktick_reply(update)
        if ticktick_reply is not None:
            return [ticktick_reply]
        telegram_user_id = self._telegram_user_id(update)
        resolved_text = self._resolve_follow_up_text(
            telegram_user_id=telegram_user_id,
            text=update.message.text,
        )
        current_timezone = self._current_timezone_for_update(update)
        context = self._build_conversation_context(
            telegram_user_id=telegram_user_id,
            text=resolved_text,
            current_timezone=current_timezone,
        )
        planned = await self._planner.plan(context)
        action_reply = await self._maybe_execute_planned_actions(
            update=update,
            planned=planned,
            telegram_user_id=telegram_user_id,
        )
        if action_reply is not None:
            return [action_reply]
        reply_text = planned.assistant_reply or self._fallback_reply(update.message.text)
        return [TelegramReply(chat_id=update.message.chat.id, text=reply_text)]

    async def update_user_timezone(self, *, user_id: int, timezone_name: str, source: str) -> None:
        self._user_timezones[user_id] = {"timezone_name": timezone_name, "source": source}
        if self._session_factory is None:
            return
        with self._session_factory() as session:
            user = session.get(User, user_id)
            if user is None:
                return
            user.current_timezone = timezone_name
            user.timezone_source = source
            session.commit()

    def _is_evening_review_reply(self, text: str) -> bool:
        action_tokens = ("做完", "完成", "改到", "改成")
        reference_tokens = ("前", "第", "最后", "那个", "这条", "这些", "这几个")
        return any(token in text for token in action_tokens) and any(token in text for token in reference_tokens)

    async def _handle_evening_review_reply(
        self,
        *,
        telegram_user_id: str | None,
        chat_id: int,
        text: str,
        current_timezone: str,
    ) -> list[TelegramReply]:
        if telegram_user_id is None or self._task_command_service is None:
            return [
                TelegramReply(
                    chat_id=chat_id,
                    text="我收到了。你这句像是在回晚间回顾，但现在还没稳定定位到具体任务，先别急，我会继续补这条链路。",
                )
            ]
        candidates = self._load_latest_evening_review_candidates(telegram_user_id=telegram_user_id)
        parsed = self._evening_review_service.parse_reply(
            reply_text=text,
            candidate_titles=[candidate["title"] for candidate in candidates],
        )
        if not parsed.completed_indices and not parsed.rescheduled_indices:
            return [
                TelegramReply(
                    chat_id=chat_id,
                    text="我收到了。你这句像是在回晚间回顾，但现在还没稳定定位到具体任务，先别急，我会继续补这条链路。",
                )
            ]

        reply_texts: list[str] = []
        for index in parsed.completed_indices:
            if index >= len(candidates):
                continue
            candidate = candidates[index]
            action = PlannedAction(
                action_type="complete_task",
                target_task_id=candidate["task_id"],
                payload={"title": candidate["title"]},
            )
            reply_texts.append(
                await self._task_command_service.execute_action(
                    telegram_user_id=telegram_user_id,
                    action=action,
                )
            )
            self._store_active_task_context(telegram_user_id=telegram_user_id, action=action)

        for index in parsed.rescheduled_indices:
            if index >= len(candidates):
                continue
            candidate = candidates[index]
            time_phrase = self._extract_reschedule_phrase(reply_text=text, index=index)
            if not time_phrase:
                reply_texts.append(f"我知道你想给“{candidate['title']}”改时间，但这句里的新时间我还没抓稳。")
                continue
            context = self._context_builder.build(
                f"任务“{candidate['title']}”改到{time_phrase}",
                current_timezone=current_timezone,
            )
            planned = await self._planner.plan(context)
            if not planned.actions:
                reply_texts.append(f"我知道你想给“{candidate['title']}”改时间，但新时间解析还没抓稳。")
                continue
            action = planned.actions[0]
            action.target_task_id = candidate["task_id"]
            action.payload.setdefault("match_title", candidate["title"])
            reply_texts.append(
                await self._task_command_service.execute_action(
                    telegram_user_id=telegram_user_id,
                    action=action,
                )
            )
            self._store_active_task_context(telegram_user_id=telegram_user_id, action=action)

        reply_text = "\n".join(item for item in reply_texts if item)
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

    async def _maybe_build_ticktick_reply(
        self,
        update: TelegramUpdate,
    ) -> TelegramReply | None:
        if self._ticktick_oauth_service is None or update.message is None or update.message.text is None:
            return None
        if not self._looks_like_ticktick_request(update.message.text):
            return None

        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is None:
            return None
        connected = await self._ticktick_oauth_service.has_connection(telegram_user_id=telegram_user_id)
        if connected:
            if self._today_brief_service is not None and self._looks_like_today_brief_request(update.message.text):
                return TelegramReply(
                    chat_id=update.message.chat.id,
                    text=await self._today_brief_service.build_today_brief(
                        telegram_user_id=telegram_user_id,
                    ),
                )
            return None

        return await self._build_ticktick_auth_reply(
            chat_id=update.message.chat.id,
            telegram_user_id=telegram_user_id,
        )

    async def _build_ticktick_auth_reply(self, *, chat_id: int, telegram_user_id: str) -> TelegramReply:
        auth_url = await self._ticktick_oauth_service.create_authorization_url(
            telegram_user_id=telegram_user_id,
            display_name=None,
        )
        if auth_url:
            return TelegramReply(
                chat_id=chat_id,
                text=f"我还没连上你的 TickTick。先点这个链接授权一下，我连好后就能继续帮你了：{auth_url}",
            )
        return TelegramReply(
            chat_id=chat_id,
            text="我知道你是在说 TickTick 相关的事，但现在还缺公开回调地址配置，所以还没法发你授权链接。",
        )

    def _looks_like_ticktick_request(self, text: str) -> bool:
        keywords = (
            "安排",
            "日程",
            "ddl",
            "deadline",
            "提醒",
            "任务",
            "todo",
            "待办",
            "ticktick",
            "list",
            "memo",
            "备忘",
            "改到",
            "改成",
            "补一句",
            "完成",
            "做完",
        )
        lowered = text.lower()
        return any(keyword in text or keyword in lowered for keyword in keywords)

    def _telegram_user_id(self, update: TelegramUpdate) -> str | None:
        if update.message is None:
            return None
        if update.message.from_ is not None:
            return str(update.message.from_.id)
        return str(update.message.chat.id)

    def _looks_like_today_brief_request(self, text: str) -> bool:
        lowered = text.lower()
        return "今天" in text and any(keyword in text or keyword in lowered for keyword in ("安排", "日程", "ddl", "deadline"))

    def _current_timezone_for_update(self, update: TelegramUpdate) -> str:
        if update.message is None or update.message.from_ is None:
            return "America/Los_Angeles"
        cached_timezone = self._user_timezones.get(update.message.from_.id, {}).get("timezone_name")
        if cached_timezone:
            return cached_timezone
        if self._session_factory is None:
            return "America/Los_Angeles"
        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(str(update.message.from_.id))
            if user is None:
                return "America/Los_Angeles"
            return user.current_timezone or "America/Los_Angeles"

    async def _maybe_execute_planned_actions(
        self,
        *,
        update: TelegramUpdate,
        planned: PlannedConversation,
        telegram_user_id: str | None,
    ) -> TelegramReply | None:
        if (
            self._task_command_service is None
            or update.message is None
            or update.message.text is None
            or not planned.actions
            or not self._looks_like_ticktick_request(update.message.text)
        ):
            return None
        if telegram_user_id is None:
            return None

        action = planned.actions[0]
        confirmation_text = await self._maybe_request_write_confirmation(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        if confirmation_text is not None:
            return TelegramReply(chat_id=update.message.chat.id, text=confirmation_text)
        self._apply_active_task_context(telegram_user_id=telegram_user_id, action=action)
        reply_text = await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        self._store_active_task_context(telegram_user_id=telegram_user_id, action=action)
        return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

    async def _maybe_handle_multiline_batch(self, update: TelegramUpdate) -> list[TelegramReply] | None:
        if update.message is None or update.message.text is None:
            return None
        lines = self._context_builder.split_lines(update.message.text)
        if len(lines) <= 1:
            return None
        telegram_user_id = self._telegram_user_id(update)
        if (
            self._ticktick_oauth_service is not None
            and telegram_user_id is not None
            and any(self._looks_like_ticktick_request(line) for line in lines)
        ):
            connected = await self._ticktick_oauth_service.has_connection(
                telegram_user_id=telegram_user_id
            )
            if not connected:
                return [
                    await self._build_ticktick_auth_reply(
                        chat_id=update.message.chat.id,
                        telegram_user_id=telegram_user_id,
                    )
                ]

        reply_texts: list[str] = []
        for line in lines:
            line_update = update.model_copy(
                deep=True,
                update={
                    "message": update.message.model_copy(
                        deep=True,
                        update={"text": line},
                    )
                },
            )
            line_replies = await self.handle_update(line_update)
            reply_texts.extend(reply.text for reply in line_replies if reply.text)

        combined_text = "\n".join(reply_texts)
        return [TelegramReply(chat_id=update.message.chat.id, text=combined_text)]

    def _resolve_follow_up_text(self, *, telegram_user_id: str | None, text: str) -> str:
        if telegram_user_id is None:
            return text
        context = self._active_task_contexts.get(telegram_user_id) or self._load_persisted_active_task_context(
            telegram_user_id=telegram_user_id
        )
        if context is None:
            return text
        title = context.get("title")
        if not title:
            return text

        stripped = text.strip()
        if self._has_explicit_task_reference(stripped):
            return stripped
        if stripped.startswith(("改到", "改成", "挪到", "推到", "提前", "延后", "放到")):
            return f"任务“{title}”{stripped}"
        if stripped.startswith(("再补一句", "补一句", "补充", "加一句")):
            return f"任务“{title}”{stripped}"
        if stripped.startswith(("做完了", "完成了", "勾掉", "勾选完成")):
            return f"任务“{title}”{stripped}"
        return stripped

    def _store_active_task_context(self, *, telegram_user_id: str, action: PlannedAction) -> None:
        title = None
        if action.action_type == "create_task":
            title = action.payload.get("title")
        elif action.action_type == "update_task":
            title = action.payload.get("title") or action.payload.get("match_title")
        elif action.action_type == "complete_task":
            title = action.payload.get("title")
        if not title:
            return
        context_payload = {
            "title": str(title).strip(),
            "task_id": action.target_task_id or "",
        }
        self._active_task_contexts[telegram_user_id] = context_payload
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.replace_active_context(
            user_id=user.id,
            context_type="active_task",
            payload_json=context_payload,
        )

    def _apply_active_task_context(self, *, telegram_user_id: str, action: PlannedAction) -> None:
        if action.target_task_id:
            return
        context = self._active_task_contexts.get(telegram_user_id) or self._load_persisted_active_task_context(
            telegram_user_id=telegram_user_id
        )
        if context is None:
            return
        task_id = context.get("task_id")
        title = context.get("title")
        if not task_id or not title:
            return
        action_title = self._action_title(action)
        if action.action_type != "update_task" and action.action_type != "complete_task":
            return
        if action_title and self._normalize_title(action_title) != self._normalize_title(title):
            return
        action.target_task_id = task_id

    def _has_explicit_task_reference(self, text: str) -> bool:
        if text.startswith(("那个", "这条", "前", "第", "最后")):
            return False
        if text.startswith(("改到", "改成", "挪到", "推到", "提前", "延后", "放到", "再补一句", "补一句", "补充", "加一句", "做完了", "完成了", "勾掉", "勾选完成")):
            return False
        return " " in text or len(text) > 12

    def _action_title(self, action: PlannedAction) -> str | None:
        if action.action_type == "create_task":
            value = action.payload.get("title")
        elif action.action_type == "update_task":
            value = action.payload.get("title") or action.payload.get("match_title")
        elif action.action_type == "complete_task":
            value = action.payload.get("title")
        else:
            value = None
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    def _normalize_title(self, value: str) -> str:
        return "".join(value.casefold().split())

    async def _maybe_handle_location_update(self, update: TelegramUpdate) -> TelegramReply | None:
        if update.message is None or update.message.location is None:
            return None
        latitude = update.message.location.get("latitude")
        longitude = update.message.location.get("longitude")
        if latitude is None or longitude is None:
            return None
        timezone_name = await self._timezone_resolver.resolve_from_location(
            latitude=float(latitude),
            longitude=float(longitude),
        )
        if not timezone_name:
            return TelegramReply(
                chat_id=update.message.chat.id,
                text="我收到了你的位置，但这次还没稳稳算出时区。你也可以直接跟我说按哪个时区提醒。",
            )
        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is not None:
            self._persist_timezone_for_telegram_user(
                telegram_user_id=telegram_user_id,
                timezone_name=timezone_name,
                source="location",
            )
        return TelegramReply(
            chat_id=update.message.chat.id,
            text=f"收到，我之后就按 {timezone_name} 这个时区陪你安排和提醒。",
        )

    async def _maybe_handle_timezone_text_update(self, update: TelegramUpdate) -> TelegramReply | None:
        if update.message is None or update.message.text is None:
            return None
        text = update.message.text.strip()
        if not text:
            return None
        timezone_name = self._timezone_resolver.resolve_from_text(text)
        if not timezone_name:
            return None
        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is not None:
            self._persist_timezone_for_telegram_user(
                telegram_user_id=telegram_user_id,
                timezone_name=timezone_name,
                source="text",
            )
        if self._looks_like_ticktick_request(text) and not self._looks_like_timezone_update_text(text):
            return None
        return TelegramReply(
            chat_id=update.message.chat.id,
            text=f"好，后面我就按 {timezone_name} 这个时区来理解今天、明天和提醒时间。",
        )

    async def _maybe_handle_snooze_request(self, update: TelegramUpdate) -> TelegramReply | None:
        if update.message is None or update.message.text is None or self._session_factory is None:
            return None
        text = update.message.text.strip()
        if not self._looks_like_snooze_request(text):
            return None
        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is None:
            return None

        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None:
                return None
            reminder_repo = ReminderRepository(session)
            source_event = reminder_repo.get_latest_for_user(
                user_id=user.id,
                event_types=["prestart_reminder", "snoozed_reminder"],
                statuses=["sent"],
            )
            if source_event is None or not source_event.payload_json:
                return None
            task_id = source_event.payload_json.get("task_id") or source_event.ticktick_task_id
            task_due_at = source_event.payload_json.get("task_due_at")
            title = source_event.payload_json.get("title") or "这条任务"
            if not task_id or not task_due_at:
                return None
            event_payload = self._reminder_service.build_snooze_event(
                task={"id": task_id, "due_at": task_due_at},
                request_text=text,
            )
            scheduled_at = self._coerce_datetime(event_payload["scheduled_at"]) or datetime.now(timezone.utc)
            reminder_repo.add(
                ReminderEvent(
                    user_id=user.id,
                    ticktick_task_id=str(task_id),
                    event_type=event_payload["event_type"],
                    scheduled_at=scheduled_at.astimezone(timezone.utc),
                    status="pending",
                    dedupe_key=f"snooze:{task_id}:{scheduled_at.astimezone(timezone.utc).isoformat()}",
                    payload_json={
                        "text": self._render_snooze_text(request_text=text, title=str(title)),
                        "task_id": str(task_id),
                        "title": str(title),
                        "task_due_at": task_due_at,
                    },
                    snoozed_from_event_id=source_event.id,
                )
            )
            session.commit()
        return TelegramReply(
            chat_id=update.message.chat.id,
            text=self._render_snooze_ack(request_text=text, title=str(title)),
        )

    def _persist_timezone_for_telegram_user(self, *, telegram_user_id: str, timezone_name: str, source: str) -> None:
        if self._session_factory is None:
            return
        with self._session_factory() as session:
            user = UserRepository(session).get_or_create(telegram_user_id=telegram_user_id)
            user.current_timezone = timezone_name
            user.timezone_source = source
            session.commit()
            user_id = user.id
        self._user_timezones[int(telegram_user_id)] = {"timezone_name": timezone_name, "source": source}
        self._user_timezones[user_id] = {"timezone_name": timezone_name, "source": source}
        if self._memory_service is not None:
            self._memory_service.upsert_fact(
                user_id=user_id,
                key="current_timezone",
                value_json={"value": timezone_name},
                memory_type=MemoryType.PREFERENCE,
                source_type=source,
            )

    def _load_latest_evening_review_candidates(self, *, telegram_user_id: str) -> list[dict[str, str]]:
        if self._session_factory is None:
            return []
        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None:
                return []
            event = ReminderRepository(session).get_latest_for_user(
                user_id=user.id,
                event_types=["evening_review"],
                statuses=["sent"],
            )
            if event is None or not event.payload_json:
                return []
            raw_candidates = event.payload_json.get("candidate_tasks") or []
            return [
                {
                    "task_id": str(item.get("task_id", "")),
                    "title": str(item.get("title", "")),
                }
                for item in raw_candidates
                if item.get("task_id") and item.get("title")
            ]

    def _extract_reschedule_phrase(self, *, reply_text: str, index: int) -> str | None:
        ordinal_tokens = {
            0: ("第一个", "第1个", "第1条", "第一条", "第一个任务"),
            1: ("第二个", "第2个", "第2条", "第二条"),
            2: ("第三个", "第3个", "第3条", "第三条"),
            3: ("第四个", "第4个", "第4条", "第四条"),
            4: ("第五个", "第5个", "第5条", "第五条"),
        }
        for token in ordinal_tokens.get(index, ()):
            match = re.search(rf"{re.escape(token)}改到(.+?)(?:，|,|$)", reply_text)
            if match:
                return match.group(1).strip()
        if index == -1:
            match = re.search(r"最后(?:一个|一条)?改到(.+?)(?:，|,|$)", reply_text)
            if match:
                return match.group(1).strip()
        return None

    def _looks_like_snooze_request(self, text: str) -> bool:
        return any(token in text for token in ("再提醒我", "稍后提醒", "先别催", "晚点提醒", "小时后再提醒", "今晚8点再提醒", "今晚 8 点再提醒"))

    def _looks_like_timezone_update_text(self, text: str) -> bool:
        return any(token in text for token in ("按", "时区", "时间", "我到", "我现在在", "以后按"))

    def _render_snooze_ack(self, *, request_text: str, title: str) -> str:
        if "1小时" in request_text or "1 小时" in request_text:
            return f"好，我 1小时后再提醒你：{title}"
        return f"好，我晚点再提醒你：{title}"

    def _render_snooze_text(self, *, request_text: str, title: str) -> str:
        if "1小时" in request_text or "1 小时" in request_text:
            return f"1小时后再提醒：{title}"
        return f"晚点再提醒：{title}"

    async def _maybe_handle_pending_confirmation(self, update: TelegramUpdate) -> TelegramReply | None:
        if update.message is None or update.message.text is None or self._task_command_service is None:
            return None
        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is None:
            return None
        context = self._load_pending_confirmation_context(telegram_user_id=telegram_user_id)
        if context is None:
            return None

        text = update.message.text.strip()
        lower_text = text.casefold()
        if any(token in text for token in ("取消", "算了", "不用了", "不要了")):
            self._clear_pending_confirmation(telegram_user_id=telegram_user_id)
            return TelegramReply(chat_id=update.message.chat.id, text="好，我先不动这条。")

        if context.get("kind") == "awaiting_reschedule":
            reply_text = await self._execute_rescheduled_confirmation(
                telegram_user_id=telegram_user_id,
                user_text=text,
                context=context,
            )
            self._clear_pending_confirmation(telegram_user_id=telegram_user_id)
            return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

        if "改时间" in text:
            self._save_pending_confirmation(
                telegram_user_id=telegram_user_id,
                payload={
                    **context,
                    "kind": "awaiting_reschedule",
                },
            )
            return TelegramReply(
                chat_id=update.message.chat.id,
                text="好，你直接把新的时间发给我就行，比如“周四下午3点”或者“明早10点”。",
            )

        if any(token in lower_text for token in ("合并", "merge", "覆盖", "改原来的", "更新原来")):
            reply_text = await self._execute_merge_confirmation(
                telegram_user_id=telegram_user_id,
                context=context,
            )
            self._clear_pending_confirmation(telegram_user_id=telegram_user_id)
            return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

        if any(token in text for token in ("继续", "就新建", "还是新建", "新建", "确认", "是")):
            reply_text = await self._execute_original_confirmation(
                telegram_user_id=telegram_user_id,
                context=context,
            )
            self._clear_pending_confirmation(telegram_user_id=telegram_user_id)
            return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

        return TelegramReply(
            chat_id=update.message.chat.id,
            text="我先停在这一步。你可以回我“合并”“继续新建”“改时间”或者“取消”。",
        )

    async def _maybe_request_write_confirmation(self, *, telegram_user_id: str, action: PlannedAction) -> str | None:
        if self._session_factory is None or self._ticktick_client is None:
            return None
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None or not user.ticktick_access_token:
            return None
        if action.action_type not in {"create_task", "update_task"}:
            return None

        tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token, since=None)
        title = self._action_title(action)
        due_at = self._coerce_datetime(action.payload.get("due_at"))

        duplicate_task = self._find_duplicate_task(tasks=tasks, title=title, exclude_task_id=action.target_task_id)
        if duplicate_task is not None and action.action_type == "create_task":
            self._save_pending_confirmation(
                telegram_user_id=telegram_user_id,
                payload={
                    "kind": "duplicate_create",
                    "candidate_task": {"task_id": duplicate_task.id, "title": duplicate_task.title},
                    "original_action": action.model_dump(),
                },
            )
            return (
                f"我看到一条和“{title}”很像的未完成任务：{duplicate_task.title}。"
                "你想继续新建、合并到原来那条，还是改时间？"
            )

        if due_at is not None:
            conflict_task = self._find_conflicting_task(
                tasks=tasks,
                due_at=due_at,
                current_timezone=user.current_timezone or "America/Los_Angeles",
                exclude_task_id=action.target_task_id,
            )
            if conflict_task is not None:
                self._save_pending_confirmation(
                    telegram_user_id=telegram_user_id,
                    payload={
                        "kind": "time_conflict",
                        "candidate_task": {"task_id": conflict_task.id, "title": conflict_task.title},
                        "original_action": action.model_dump(),
                    },
                )
                return (
                    f"这个时间和你已有的安排“{conflict_task.title}”撞上了。"
                    "你想继续、合并到原来那条，还是改时间？"
                )
        return None

    async def _execute_original_confirmation(self, *, telegram_user_id: str, context: dict) -> str:
        action = PlannedAction.model_validate(context["original_action"])
        return await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )

    async def _execute_merge_confirmation(self, *, telegram_user_id: str, context: dict) -> str:
        original_action = PlannedAction.model_validate(context["original_action"])
        candidate = context.get("candidate_task") or {}
        payload = {
            **original_action.payload,
            "match_title": candidate.get("title") or original_action.payload.get("title"),
        }
        payload.pop("title", None)
        action = PlannedAction(
            action_type="update_task",
            target_task_id=candidate.get("task_id"),
            payload=payload,
        )
        return await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )

    async def _execute_rescheduled_confirmation(
        self,
        *,
        telegram_user_id: str,
        user_text: str,
        context: dict,
    ) -> str:
        original_action = PlannedAction.model_validate(context["original_action"])
        title = original_action.payload.get("title") or context.get("candidate_task", {}).get("title")
        if not title:
            return "我知道你想改时间，但这条任务标题我这次没抓稳。"
        if original_action.action_type == "create_task":
            prompt_text = f"{user_text}提醒我{title}"
        else:
            prompt_text = f"任务“{title}”改到{user_text}"
        current_timezone = "America/Los_Angeles"
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is not None:
            current_timezone = user.current_timezone or current_timezone
        planned = await self._planner.plan(
            self._context_builder.build(prompt_text, current_timezone=current_timezone)
        )
        if not planned.actions:
            return f"我知道你想改“{title}”的时间，但这句时间我还没抓稳。"
        action = planned.actions[0]
        candidate = context.get("candidate_task") or {}
        if original_action.action_type != "create_task":
            action.target_task_id = candidate.get("task_id")
            action.payload.setdefault("match_title", candidate.get("title") or title)
        return await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )

    def _save_pending_confirmation(self, *, telegram_user_id: str, payload: dict) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.replace_active_context(
            user_id=user.id,
            context_type="pending_confirmation",
            payload_json=payload,
        )

    def _load_pending_confirmation_context(self, *, telegram_user_id: str) -> dict | None:
        if self._memory_service is None:
            return None
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return None
        context = self._memory_service.get_active_context(user_id=user.id, context_type="pending_confirmation")
        return None if context is None else dict(context.payload_json or {})

    def _clear_pending_confirmation(self, *, telegram_user_id: str) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.clear_active_context(user_id=user.id, context_type="pending_confirmation")

    def _find_duplicate_task(self, *, tasks: list, title: str | None, exclude_task_id: str | None) -> object | None:
        if not title:
            return None
        for task in tasks:
            if task.completed or task.status == 2:
                continue
            if exclude_task_id and task.id == exclude_task_id:
                continue
            if self._normalize_title(task.title) == self._normalize_title(title):
                return task
            if self._duplicate_detector.is_probable_duplicate(task.title, title):
                return task
        return None

    def _find_conflicting_task(
        self,
        *,
        tasks: list,
        due_at: datetime,
        current_timezone: str,
        exclude_task_id: str | None,
    ) -> object | None:
        due_iso = due_at.isoformat()
        for task in tasks:
            if task.completed or task.status == 2:
                continue
            if exclude_task_id and task.id == exclude_task_id:
                continue
            task_due_at = self._parse_ticktick_datetime(task.dueDate or task.startDate, timezone_name=current_timezone)
            if task_due_at is None:
                continue
            if self._conflict_detector.has_conflict(due_iso, task_due_at.isoformat()):
                return task
        return None

    def _coerce_datetime(self, value: object | None) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    def _parse_ticktick_datetime(self, raw: str | None, *, timezone_name: str) -> datetime | None:
        if not raw:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
            try:
                parsed = datetime.strptime(raw, fmt)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def _build_conversation_context(
        self,
        *,
        telegram_user_id: str | None,
        text: str,
        current_timezone: str,
    ):
        if telegram_user_id is None or self._memory_service is None:
            return self._context_builder.build(text, current_timezone=current_timezone)
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return self._context_builder.build(text, current_timezone=current_timezone)
        return self._memory_service.build_context(
            user_id=user.id,
            text=text,
            current_timezone=current_timezone,
            candidate_tasks=self._candidate_tasks_for_telegram_user(telegram_user_id=telegram_user_id),
        )

    def _candidate_tasks_for_telegram_user(self, *, telegram_user_id: str) -> list[str]:
        tasks: list[str] = []
        active = self._active_task_contexts.get(telegram_user_id) or self._load_persisted_active_task_context(
            telegram_user_id=telegram_user_id
        )
        if active and active.get("title"):
            tasks.append(str(active["title"]))
        return tasks

    def _load_persisted_active_task_context(self, *, telegram_user_id: str) -> dict[str, str] | None:
        if self._memory_service is None:
            return None
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return None
        context = self._memory_service.get_active_context(user_id=user.id, context_type="active_task")
        if context is None:
            return None
        payload = context.payload_json or {}
        if not payload.get("title"):
            return None
        loaded = {"title": str(payload.get("title")), "task_id": str(payload.get("task_id", ""))}
        self._active_task_contexts[telegram_user_id] = loaded
        return loaded

    def _get_user_by_telegram_user_id(self, telegram_user_id: str) -> User | None:
        if self._session_factory is None:
            return None
        with self._session_factory() as session:
            return UserRepository(session).get_by_telegram_user_id(telegram_user_id)

    def _record_turn_summary(self, *, telegram_user_id: str | None, text: str) -> None:
        if telegram_user_id is None or self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.save_conversation_summary(
            user_id=user.id,
            summary_text=f"最近提到：{text.strip()[:120]}",
        )
        self._memory_service.upsert_fact(
            user_id=user.id,
            key="default_language",
            value_json={"value": user.default_language or "zh-CN"},
            memory_type=MemoryType.PREFERENCE,
            source_type="system",
        )


class NoopConversationService(ConversationService):
    def __init__(self) -> None:
        super().__init__(context_builder=ContextBuilder(), planner=OpenAIPlanner(client=None))

    async def handle_update(self, update: TelegramUpdate) -> list[TelegramReply]:
        return []
