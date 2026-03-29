from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.action_log import ActionLog
from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.domain.schemas import (
    ConversationContext,
    PlannedAction,
    PlannedConversation,
    PlannedQueryIntent,
    PlannedTaskWriteIntent,
    TelegramReply,
)
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner
from ticktick_telegram_assistant.repositories.action_logs import ActionLogRepository
from ticktick_telegram_assistant.repositories.reminders import ReminderRepository
from ticktick_telegram_assistant.repositories.users import UserRepository
from ticktick_telegram_assistant.services.conflict_detector import ConflictDetector
from ticktick_telegram_assistant.services.context_builder import ContextBuilder
from ticktick_telegram_assistant.services.duplicate_detector import DuplicateDetector
from ticktick_telegram_assistant.services.evening_review_service import EveningReviewService
from ticktick_telegram_assistant.services.memory_service import MemoryService
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer
from ticktick_telegram_assistant.services.reminder_service import ReminderService
from ticktick_telegram_assistant.services.task_command_service import TaskCommandExecutionCache
from ticktick_telegram_assistant.services.time_interpreter import TimeInterpreter
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
        task_query_service=None,
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
        self._task_query_service = task_query_service
        self._task_command_service = task_command_service
        self._session_factory = session_factory
        self._reminder_service = reminder_service or ReminderService()
        self._timezone_resolver = timezone_resolver or TimezoneResolver()
        self._memory_service = memory_service
        self._ticktick_client = ticktick_client
        self._duplicate_detector = duplicate_detector or DuplicateDetector()
        self._conflict_detector = conflict_detector or ConflictDetector()
        self._message_renderer = MessageRenderer()
        self._user_timezones: dict[int, dict[str, str]] = {}
        self._active_task_contexts: dict[str, dict[str, str]] = {}

    async def handle_update(self, update: TelegramUpdate) -> list[TelegramReply]:
        trace: dict[str, object] = {"status": "noop"}
        try:
            replies = await self._handle_update(update, trace=trace)
        except Exception as exc:
            upstream_reply = self._build_ticktick_upstream_error_reply(update=update, exc=exc)
            if upstream_reply is not None:
                trace["status"] = "ticktick_upstream_error"
                trace["execution_result_json"] = {
                    "error": str(exc),
                    "reply_texts": [upstream_reply.text],
                }
                self._write_action_log(update=update, trace=trace, replies=[upstream_reply])
                return [upstream_reply]
            self._write_action_log(
                update=update,
                trace={
                    "status": "error",
                    "parsed_plan_json": trace.get("parsed_plan_json"),
                    "execution_result_json": {"error": str(exc)},
                },
                replies=[],
            )
            raise
        self._write_action_log(update=update, trace=trace, replies=replies)
        return replies

    async def _handle_update(
        self,
        update: TelegramUpdate,
        *,
        skip_pending_contexts: bool = False,
        skip_multiline_batch: bool = False,
        execution_cache: TaskCommandExecutionCache | None = None,
        trace: dict[str, object] | None = None,
    ) -> list[TelegramReply]:
        if update.message is None:
            return []
        location_reply = await self._maybe_handle_location_update(update)
        if location_reply is not None:
            if trace is not None:
                trace["status"] = "location_updated"
            return [location_reply]
        if update.message.text is None:
            return []
        update = self._normalize_text_update(update)
        self._record_turn_summary(
            telegram_user_id=self._telegram_user_id(update),
            text=update.message.text,
        )
        assistant_transcript_reply = self._maybe_handle_pasted_assistant_transcript(update)
        if assistant_transcript_reply is not None:
            if trace is not None:
                trace["status"] = "assistant_transcript"
            return [assistant_transcript_reply]
        timezone_text_reply = await self._maybe_handle_timezone_text_update(update)
        if timezone_text_reply is not None:
            if trace is not None:
                trace["status"] = "timezone_updated"
            return [timezone_text_reply]
        snooze_reply = await self._maybe_handle_snooze_request(update)
        if snooze_reply is not None:
            if trace is not None:
                trace["status"] = "snoozed"
            return [snooze_reply]
        if not skip_pending_contexts:
            confirmation_reply = await self._maybe_handle_pending_confirmation(update)
            if confirmation_reply is not None:
                if trace is not None:
                    trace["status"] = "confirmation_resolved"
                return [confirmation_reply]
            query_reply = await self._maybe_handle_pending_query(update)
            if query_reply is not None:
                if trace is not None:
                    trace["status"] = "query_follow_up"
                return [query_reply]
        if not skip_multiline_batch:
            batch_replies = await self._maybe_handle_multiline_batch(update)
            if batch_replies is not None:
                if trace is not None:
                    trace["status"] = "batch_completed"
                return batch_replies
        if self._is_evening_review_reply(update.message.text):
            if trace is not None:
                trace["status"] = "evening_review"
            return await self._handle_evening_review_reply(
                telegram_user_id=self._telegram_user_id(update),
                chat_id=update.message.chat.id,
                text=update.message.text,
                current_timezone=self._current_timezone_for_update(update),
        )
        memo_cleanup_reply = await self._maybe_handle_memo_cleanup_reply(update)
        if memo_cleanup_reply is not None:
            if trace is not None:
                trace["status"] = "memo_cleanup"
            return [memo_cleanup_reply]
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
        if self._planned_conversation_is_empty(planned):
            fallback_planned = self._maybe_build_rule_based_task_write(
                text=resolved_text,
                current_timezone=current_timezone,
            )
            if fallback_planned is not None:
                planned = fallback_planned
        if trace is not None:
            trace["parsed_plan_json"] = planned.model_dump(mode="json")
        query_reply = await self._maybe_execute_planned_query(
            update=update,
            planned=planned,
            telegram_user_id=telegram_user_id,
            trace=trace,
        )
        if query_reply is not None:
            return [query_reply]
        action_reply = await self._maybe_execute_planned_actions(
            update=update,
            planned=planned,
            telegram_user_id=telegram_user_id,
            context=context,
            execution_cache=execution_cache,
            trace=trace,
        )
        if action_reply is not None:
            return [action_reply]
        ticktick_reply = await self._maybe_build_ticktick_reply(update)
        if ticktick_reply is not None:
            if trace is not None:
                trace["status"] = "ticktick_fallback"
            return [ticktick_reply]
        reply_text = planned.assistant_reply or self._fallback_reply(update.message.text)
        self._maybe_save_pending_query(
            telegram_user_id=telegram_user_id,
            reply_text=reply_text,
            planned=planned,
        )
        if trace is not None:
            trace["status"] = "assistant_reply"
        return [TelegramReply(chat_id=update.message.chat.id, text=reply_text)]

    def _normalize_text_update(self, update: TelegramUpdate) -> TelegramUpdate:
        if update.message is None or update.message.text is None:
            return update
        normalized_text = "\n".join(
            self._normalize_message_line(line)
            for line in update.message.text.splitlines()
            if line.strip()
        ).strip()
        if not normalized_text or normalized_text == update.message.text:
            return update
        return update.model_copy(
            deep=True,
            update={
                "message": update.message.model_copy(
                    deep=True,
                    update={"text": normalized_text},
                )
            },
        )

    def _normalize_message_line(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^\s*(?:[-*•·●▪︎◦]+|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"已完成\s*$", "完成了", cleaned)
        cleaned = re.sub(r"已做完\s*$", "做完了", cleaned)
        cleaned = self._normalize_move_to_schedule(cleaned)
        return cleaned

    def _normalize_move_to_schedule(self, text: str) -> str:
        time_like_pattern = (
            r"(今天|今晚|今早|明天|明早|明晚|后天|下周|这周|本周|周末|月底|月初|"
            r"\d{1,2}月\d{1,2}(?:号|日)?|\d{1,2}号|周[一二三四五六日天]|上午|中午|下午|晚上)"
        )
        return re.sub(rf"移动到(?={time_like_pattern})", "改到", text)

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
            self._record_successful_action_memory(
                telegram_user_id=telegram_user_id,
                action=action,
                source_text=candidate["title"],
            )

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
            self._record_successful_action_memory(
                telegram_user_id=telegram_user_id,
                action=action,
                source_text=f"任务“{candidate['title']}”改到{time_phrase}",
            )

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
                return await self._build_connected_today_brief_reply(
                    chat_id=update.message.chat.id,
                    telegram_user_id=telegram_user_id,
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
        if self._looks_like_today_brief_request(text):
            return True
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
        if any(keyword in text or keyword in lowered for keyword in keywords):
            return True
        return bool(re.search(r"(今天|明天|后天|今晚|下周|这两周|月底前|周[一二三四五六日天]|上午|中午|下午|晚上|\d{1,2}点)", text))

    def _telegram_user_id(self, update: TelegramUpdate) -> str | None:
        if update.message is None:
            return None
        if update.message.from_ is not None:
            return str(update.message.from_.id)
        return str(update.message.chat.id)

    def _looks_like_today_brief_request(self, text: str) -> bool:
        lowered = text.lower()
        if "今天" not in text and "今日" not in text:
            return False
        if any(keyword in text or keyword in lowered for keyword in ("安排", "日程", "ddl", "deadline")):
            return True
        return bool(
            re.search(
                r"(今天|今日).*(要做什么|做什么|干什么|干嘛|该做什么|有什么事|有哪些事|有什么要做)",
                text,
            )
        )

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
        context: ConversationContext,
        execution_cache: TaskCommandExecutionCache | None = None,
        allow_non_ticktick_request: bool = False,
        trace: dict[str, object] | None = None,
    ) -> TelegramReply | None:
        if planned.requires_confirmation:
            if update.message is None or telegram_user_id is None:
                return None
            if trace is not None:
                trace["status"] = "confirmation_pending"
            return self._save_planner_confirmation(
                update=update,
                telegram_user_id=telegram_user_id,
                planned=planned,
                context=context,
            )
        if (
            self._task_command_service is None
            or update.message is None
            or update.message.text is None
        ):
            return None
        if telegram_user_id is None:
            return None
        if not planned.actions:
            return None
        auth_reply = await self._maybe_require_ticktick_connection(
            chat_id=update.message.chat.id,
            telegram_user_id=telegram_user_id,
        )
        if auth_reply is not None:
            if trace is not None:
                trace["status"] = "ticktick_auth_required"
            return auth_reply
        if (
            not allow_non_ticktick_request
            and not self._looks_like_ticktick_request(update.message.text)
            and not any(action.action_type in {"create_task", "update_task", "complete_task"} for action in planned.actions)
        ):
            return None

        action = planned.actions[0]
        confirmation_text = await self._maybe_request_write_confirmation(
            telegram_user_id=telegram_user_id,
            action=action,
            execution_cache=execution_cache,
        )
        if confirmation_text is not None:
            return TelegramReply(chat_id=update.message.chat.id, text=confirmation_text)
        self._apply_active_task_context(telegram_user_id=telegram_user_id, action=action)
        reply_text = await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
            execution_cache=execution_cache,
            source_text=update.message.text,
            retry_dedupe_key=self._build_task_retry_dedupe_key(update=update),
        )
        self._store_active_task_context(telegram_user_id=telegram_user_id, action=action)
        if trace is not None:
            trace["status"] = "task_action"
        self._record_successful_action_memory(
            telegram_user_id=telegram_user_id,
            action=action,
            source_text=update.message.text,
        )
        return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

    async def _maybe_execute_planned_query(
        self,
        *,
        update: TelegramUpdate,
        planned: PlannedConversation,
        telegram_user_id: str | None,
        trace: dict[str, object] | None = None,
    ) -> TelegramReply | None:
        if (
            planned.intent_type != "query"
            or planned.query is None
            or update.message is None
            or telegram_user_id is None
        ):
            return None
        auth_reply = await self._maybe_require_ticktick_connection(
            chat_id=update.message.chat.id,
            telegram_user_id=telegram_user_id,
        )
        if auth_reply is not None:
            if trace is not None:
                trace["status"] = "ticktick_auth_required"
            return auth_reply
        if planned.assistant_reply and self._looks_like_query_follow_up_prompt(planned.assistant_reply):
            return None
        if self._task_query_service is None:
            if planned.query.time_scope == "today":
                reply = await self._build_connected_today_brief_reply(
                    chat_id=update.message.chat.id,
                    telegram_user_id=telegram_user_id,
                )
                if trace is not None:
                    trace["status"] = "query_reply"
                return reply
            return TelegramReply(
                chat_id=update.message.chat.id,
                text="我知道你是在查 TickTick 里的安排，但这条查询链路现在还没完全接好。",
            )
        reply_text = await self._task_query_service.build_query_reply(
            telegram_user_id=telegram_user_id,
            query=planned.query,
        )
        if trace is not None:
            trace["status"] = "query_reply"
        return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

    async def _maybe_require_ticktick_connection(
        self,
        *,
        chat_id: int,
        telegram_user_id: str,
    ) -> TelegramReply | None:
        if self._ticktick_oauth_service is None:
            return None
        connected = await self._ticktick_oauth_service.has_connection(telegram_user_id=telegram_user_id)
        if connected:
            return None
        return await self._build_ticktick_auth_reply(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
        )

    def _save_planner_confirmation(
        self,
        *,
        update: TelegramUpdate,
        telegram_user_id: str,
        planned: PlannedConversation,
        context: ConversationContext,
    ) -> TelegramReply:
        if update.message is None:
            return TelegramReply(chat_id=0, text=planned.assistant_reply or "我需要你再确认一下。")
        self._save_pending_confirmation(
            telegram_user_id=telegram_user_id,
            payload={
                "kind": "planner_confirmation",
                "assistant_reply": planned.assistant_reply,
                "planned_context": context.model_dump(),
            },
        )
        return TelegramReply(
            chat_id=update.message.chat.id,
            text=planned.assistant_reply or "我需要你再确认一下。",
        )

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

        existing_batch_context = self._load_pending_batch_context(telegram_user_id=telegram_user_id)
        reply_texts: list[str] = []
        pending_batch_entries: list[dict] = list(existing_batch_context.get("entries") or []) if existing_batch_context else []
        execution_cache = TaskCommandExecutionCache()
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
            try:
                line_replies = await self._handle_update(
                    line_update,
                    skip_pending_contexts=True,
                    skip_multiline_batch=True,
                    execution_cache=execution_cache,
                )
            except Exception as exc:
                batch_error_reply = self._build_ticktick_batch_error_text(exc=exc)
                if batch_error_reply is None:
                    raise
                reply_texts.append(batch_error_reply)
                break
            reply_texts.extend(reply.text for reply in line_replies if reply.text)
            confirmation_context = self._load_pending_confirmation_context(telegram_user_id=telegram_user_id)
            if confirmation_context is not None:
                pending_batch_entries.append(
                    {
                        "context_type": "pending_confirmation",
                        "line_text": line,
                        "payload": confirmation_context,
                    }
                )
                self._clear_pending_confirmation(telegram_user_id=telegram_user_id)
            query_context = self._load_pending_query_context(telegram_user_id=telegram_user_id)
            if query_context is not None:
                pending_batch_entries.append(
                    {
                        "context_type": "pending_query",
                        "line_text": line,
                        "payload": query_context,
                    }
                )
                self._clear_pending_query(telegram_user_id=telegram_user_id)

        if pending_batch_entries:
            self._save_pending_batch(
                telegram_user_id=telegram_user_id,
                payload={
                    "kind": "multiline_batch_pending",
                    "entries": pending_batch_entries,
                },
            )

        combined_text = "\n".join(reply_texts)
        return [TelegramReply(chat_id=update.message.chat.id, text=combined_text)]

    def _planned_conversation_is_empty(self, planned: PlannedConversation) -> bool:
        return (
            planned.intent_type is None
            and planned.query is None
            and planned.reminder_control is None
            and planned.clarification is None
            and planned.task_write is None
            and not planned.actions
            and not planned.requires_confirmation
            and planned.assistant_reply is None
        )

    def _maybe_build_rule_based_task_write(
        self,
        *,
        text: str,
        current_timezone: str,
    ) -> PlannedConversation | None:
        complete_match = re.fullmatch(r"(?P<title>.+?)(?:完成了|做完了|已完成|已做完|勾掉了?)", text.strip())
        if complete_match is not None:
            title = self._sanitize_rule_based_title(complete_match.group("title"))
            if title:
                return PlannedConversation(
                    intent_type="task_write",
                    task_write=PlannedTaskWriteIntent(
                        write_type="complete",
                        target_title=title,
                        summary=f"完成任务：{title}",
                    ),
                    actions=[
                        PlannedAction(
                            action_type="complete_task",
                            payload={"title": title},
                        )
                    ],
                )

        update_match = re.fullmatch(r"(?P<title>.+?)改到(?P<when>.+)", text.strip())
        if update_match is not None:
            title = self._sanitize_rule_based_title(update_match.group("title"))
            raw_time = self._clean_optional_text(update_match.group("when"))
            parsed_time = self._parse_rule_based_time(raw_time=raw_time, current_timezone=current_timezone)
            if title and raw_time and parsed_time is not None:
                payload: dict[str, object] = {
                    "match_title": title,
                    "semantic_type": parsed_time.semantic_type,
                    "raw_nl_time": raw_time,
                }
                if parsed_time.semantic_type == "explicit_time" and parsed_time.due_at is not None:
                    payload["due_at"] = parsed_time.due_at.isoformat()
                elif parsed_time.semantic_type == "windowed":
                    if parsed_time.window_start is not None:
                        payload["window_start"] = parsed_time.window_start.isoformat()
                    if parsed_time.window_end is not None:
                        payload["window_end"] = parsed_time.window_end.isoformat()
                return PlannedConversation(
                    intent_type="task_write",
                    task_write=PlannedTaskWriteIntent(
                        write_type="update",
                        target_title=title,
                        summary=f"更新时间：{title}",
                    ),
                    actions=[
                        PlannedAction(
                            action_type="update_task",
                            payload=payload,
                        )
                    ],
                )
        return None

    def _sanitize_rule_based_title(self, text: str | None) -> str | None:
        cleaned = self._clean_optional_text(text)
        if cleaned is None:
            return None
        cleaned = cleaned.strip("“”\"'` ")
        return cleaned or None

    def _build_task_retry_dedupe_key(self, *, update: TelegramUpdate) -> str | None:
        if update.message is None or update.message.text is None:
            return None
        fingerprint = hashlib.sha1(update.message.text.encode("utf-8")).hexdigest()[:12]
        return f"ticktick_write_retry:{update.message.chat.id}:{update.message.message_id}:{fingerprint}"

    def _parse_rule_based_time(self, *, raw_time: str | None, current_timezone: str):
        if raw_time is None:
            return None
        try:
            now = datetime.now(ZoneInfo(current_timezone))
        except Exception:
            now = datetime.now().astimezone()
        parsed = TimeInterpreter().parse(raw_time, now=now)
        if parsed.semantic_type == "memo":
            return None
        return parsed

    def _maybe_handle_pasted_assistant_transcript(
        self,
        update: TelegramUpdate,
    ) -> TelegramReply | None:
        if update.message is None or update.message.text is None:
            return None
        if not self._looks_like_pasted_assistant_transcript(update.message.text):
            return None
        return TelegramReply(
            chat_id=update.message.chat.id,
            text=(
                "这段看起来是我之前回你的内容，不是新的任务指令。"
                "我已经把它当成问题样本记下了，这次不会往 TickTick 里乱动。"
                "你要继续处理原任务的话，直接把原任务本身发给我就行。"
            ),
        )

    def _looks_like_pasted_assistant_transcript(self, text: str) -> bool:
        lines = [line.strip() for line in self._context_builder.split_lines(text) if line.strip()]
        if len(lines) < 2:
            return False
        transcript_like = sum(1 for line in lines if self._looks_like_assistant_reply_line(line))
        return transcript_like >= max(2, len(lines) - 1)

    def _looks_like_assistant_reply_line(self, text: str) -> bool:
        markers = (
            "我收到了你想",
            "我看懂你是在",
            "我还没连上你的 TickTick",
            "TickTick 执行链路还没接上",
            "现在 Telegram 收发已经打通",
            "先不冒险帮你写入",
            "所以这一步我先不乱动",
            "我需要你再确认一下",
            "基础服务已经起来了",
            "我收到你的话了",
            "真实执行链路还在补",
            "我已经收到了你要看今天安排的意思",
        )
        return any(marker in text for marker in markers)

    def _build_ticktick_upstream_error_reply(
        self,
        *,
        update: TelegramUpdate,
        exc: Exception,
    ) -> TelegramReply | None:
        if update.message is None or not self._is_ticktick_upstream_error(exc):
            return None
        return TelegramReply(
            chat_id=update.message.chat.id,
            text=(
                "TickTick 这边刚刚有点不稳定，我先没乱动。"
                "你过一会儿再问我一次，或者把这条再发我一次，我就继续帮你。"
            ),
        )

    def _build_ticktick_batch_error_text(self, *, exc: Exception) -> str | None:
        if not self._is_ticktick_upstream_error(exc):
            return None
        return (
            "TickTick 这边刚刚有点不稳定，前面已经处理到的我先保留，"
            "后面的我先没乱动。你过一会儿把剩下的再发我一次，我继续接着帮你。"
        )

    def _is_ticktick_upstream_error(self, exc: Exception) -> bool:
        seen: set[int] = set()
        current: Exception | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, httpx.HTTPStatusError):
                request_url = str(current.request.url) if current.request is not None else ""
                if "ticktick.com" in request_url and 500 <= current.response.status_code < 600:
                    return True
            elif isinstance(current, httpx.RequestError):
                request_url = str(current.request.url) if current.request is not None else ""
                if "ticktick.com" in request_url:
                    return True
            current = current.__cause__ or current.__context__
        return False

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
        action_title = self._target_lookup_title(action)
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

    def _target_lookup_title(self, action: PlannedAction) -> str | None:
        if action.action_type == "update_task":
            value = action.payload.get("match_title") or action.payload.get("title")
        elif action.action_type == "complete_task":
            value = action.payload.get("title")
        else:
            value = action.payload.get("title")
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    def _proposed_title(self, action: PlannedAction) -> str | None:
        value = action.payload.get("title")
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

    def _load_latest_memo_cleanup_candidates(self, *, telegram_user_id: str) -> list[dict[str, str]]:
        if self._session_factory is None:
            return []
        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None:
                return []
            event = ReminderRepository(session).get_latest_for_user(
                user_id=user.id,
                event_types=["memo_cleanup"],
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
            batch_context = self._load_pending_batch_context(telegram_user_id=telegram_user_id)
            if batch_context is None:
                return None

            text = update.message.text.strip()
            if len(self._context_builder.split_lines(text)) > 1:
                self._clear_pending_batch(telegram_user_id=telegram_user_id)
                return None
            if any(token in text for token in ("取消", "算了", "不用了", "不要了")):
                self._clear_pending_batch(telegram_user_id=telegram_user_id)
                return TelegramReply(chat_id=update.message.chat.id, text="好，我先不动这些待确认项。")

            batch_entries = batch_context.get("entries") or []
            selected_index = self._extract_candidate_index(text=text, candidate_count=len(batch_entries))
            if selected_index is None and len(batch_entries) == 1 and self._looks_like_pending_batch_continue(text):
                selected_index = 0
            if selected_index is None:
                return TelegramReply(
                    chat_id=update.message.chat.id,
                    text="我先停在这一步。你可以直接回我“第1条继续”“第二条继续”，或者回“取消”。",
                )

            selected_entry = batch_entries[selected_index]
            remaining_entries = batch_entries[:selected_index] + batch_entries[selected_index + 1 :]
            if remaining_entries:
                self._save_pending_batch(
                    telegram_user_id=telegram_user_id,
                    payload={
                        "kind": "multiline_batch_pending",
                        "entries": remaining_entries,
                    },
                )
            else:
                self._clear_pending_batch(telegram_user_id=telegram_user_id)
            return await self._resume_pending_batch_entry(
                telegram_user_id=telegram_user_id,
                chat_id=update.message.chat.id,
                text=self._strip_pending_batch_selection_text(text) or "继续",
                entry=selected_entry,
            )

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

        if context.get("kind") == "awaiting_memo_schedule":
            reply_text = await self._execute_memo_schedule_candidate(
                telegram_user_id=telegram_user_id,
                user_text=text,
                candidate=context.get("candidate_task") or {},
            )
            self._clear_pending_confirmation(telegram_user_id=telegram_user_id)
            return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

        if context.get("kind") == "task_disambiguation":
            candidate_tasks = context.get("candidate_tasks") or []
            selected_index = self._extract_candidate_index(text=text, candidate_count=len(candidate_tasks))
            if selected_index is None:
                return TelegramReply(
                    chat_id=update.message.chat.id,
                    text="我先停在这一步。你可以直接回我“第一条”“第二条”“最后一个”，或者回“取消”。",
                )
            reply_text = await self._execute_task_disambiguation_confirmation(
                telegram_user_id=telegram_user_id,
                context=context,
                selected_index=selected_index,
            )
            self._clear_pending_confirmation(telegram_user_id=telegram_user_id)
            return TelegramReply(chat_id=update.message.chat.id, text=reply_text)

        if context.get("kind") == "planner_confirmation" and any(
            token in text for token in ("继续", "就它", "确认", "是", "好", "可以", "行")
        ):
            reply_text = await self._execute_planner_confirmation(
                telegram_user_id=telegram_user_id,
                user_text=text,
                context=context,
                chat_id=update.message.chat.id,
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

    async def _resume_pending_batch_entry(
        self,
        *,
        telegram_user_id: str,
        chat_id: int,
        text: str,
        entry: dict,
    ) -> TelegramReply:
        context_type = entry.get("context_type")
        payload = entry.get("payload") or {}
        if context_type == "pending_confirmation":
            self._save_pending_confirmation(
                telegram_user_id=telegram_user_id,
                payload=payload,
            )
            response = await self._maybe_handle_pending_confirmation(
                TelegramUpdate(
                    update_id=0,
                    message=TelegramMessage(
                        message_id=0,
                        chat=TelegramChat(id=chat_id, type="private"),
                        text=text,
                    ),
                )
            )
            if response is not None:
                return response
            return TelegramReply(
                chat_id=chat_id,
                text="这条我先接住了，但还没法自动继续。你可以把这条单独再发我一次。",
            )
        if context_type == "pending_query":
            self._save_pending_query(
                telegram_user_id=telegram_user_id,
                payload=payload,
            )
            response = await self._maybe_handle_pending_query(
                TelegramUpdate(
                    update_id=0,
                    message=TelegramMessage(
                        message_id=0,
                        chat=TelegramChat(id=chat_id, type="private"),
                        text=text or "对",
                    ),
                )
            )
            if response is not None:
                return response
            return TelegramReply(
                chat_id=chat_id,
                text="这条我先记住了，但还没准备好自动继续。你可以直接把你的补充发给我。",
            )
        return TelegramReply(
            chat_id=chat_id,
            text="这条待确认内容我没法继续了，你可以重新发一次。",
        )

    async def _execute_planner_confirmation(
        self,
        *,
        telegram_user_id: str,
        user_text: str,
        context: dict,
        chat_id: int,
    ) -> str:
        planned_context = context.get("planned_context") or {}
        if not planned_context:
            return "我知道你想继续，但这条待确认上下文已经丢了。"
        stored_context = ConversationContext.model_validate(planned_context)
        resumed_context = self._context_builder.build(
            f"{stored_context.user_text}\n用户确认：{user_text}",
            current_timezone=stored_context.current_timezone,
            memory_items=stored_context.memory_items,
            recent_conversation_summaries=stored_context.recent_conversation_summaries,
            candidate_tasks=stored_context.candidate_tasks,
        )
        planned = await self._planner.plan(resumed_context)
        response = await self._maybe_execute_planned_actions(
            update=TelegramUpdate(
                update_id=0,
                message=TelegramMessage(
                    message_id=0,
                    chat=TelegramChat(id=chat_id, type="private"),
                    text=user_text,
                ),
            ),
            planned=planned,
            telegram_user_id=telegram_user_id,
            context=resumed_context,
            allow_non_ticktick_request=True,
        )
        if response is None:
            return planned.assistant_reply or "我已经记下你的确认，但这条还是没法稳定执行。"
        return response.text

    async def _maybe_handle_pending_query(self, update: TelegramUpdate) -> TelegramReply | None:
        if update.message is None or update.message.text is None:
            return None
        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is None:
            return None
        context = self._load_pending_query_context(telegram_user_id=telegram_user_id)
        if context is None:
            return None

        text = update.message.text.strip()
        lowered = text.casefold()
        if any(token in text for token in ("取消", "算了", "不用了", "不要了")):
            self._clear_pending_query(telegram_user_id=telegram_user_id)
            return TelegramReply(chat_id=update.message.chat.id, text="好，我先不继续这条。")

        if context.get("kind") == "today_brief" and any(
            token in lowered for token in ("对", "是", "好", "好的", "行", "可以", "嗯")
        ):
            self._clear_pending_query(telegram_user_id=telegram_user_id)
            return await self._build_connected_today_brief_reply(
                chat_id=update.message.chat.id,
                telegram_user_id=telegram_user_id,
            )
        if context.get("kind") == "structured_query" and any(
            token in lowered for token in ("对", "是", "好", "好的", "行", "可以", "嗯")
        ):
            query_payload = context.get("query") or {}
            query = PlannedQueryIntent.model_validate(query_payload)
            self._clear_pending_query(telegram_user_id=telegram_user_id)
            auth_reply = await self._maybe_require_ticktick_connection(
                chat_id=update.message.chat.id,
                telegram_user_id=telegram_user_id,
            )
            if auth_reply is not None:
                return auth_reply
            if self._task_query_service is None:
                return None
            return TelegramReply(
                chat_id=update.message.chat.id,
                text=await self._task_query_service.build_query_reply(
                    telegram_user_id=telegram_user_id,
                    query=query,
                ),
            )

        return None

    async def _maybe_handle_memo_cleanup_reply(self, update: TelegramUpdate) -> TelegramReply | None:
        if update.message is None or update.message.text is None or self._task_command_service is None:
            return None
        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is None:
            return None
        candidates = self._load_latest_memo_cleanup_candidates(telegram_user_id=telegram_user_id)
        if not candidates:
            return None
        text = update.message.text.strip()
        if any(token in text for token in ("先都留着", "都留着", "先留着", "都先留着")):
            return TelegramReply(
                chat_id=update.message.chat.id,
                text="好，那我先继续把这些留在备忘池里，等你想安排时间时再叫我。",
            )
        candidate_index = self._extract_candidate_index(text=text, candidate_count=len(candidates))
        if candidate_index is None:
            return None
        candidate = candidates[candidate_index]
        time_phrase = self._extract_schedule_phrase(text=text)
        if time_phrase:
            reply_text = await self._execute_memo_schedule_candidate(
                telegram_user_id=telegram_user_id,
                user_text=time_phrase,
                candidate=candidate,
            )
            return TelegramReply(chat_id=update.message.chat.id, text=reply_text)
        if any(token in text for token in ("变成任务", "安排时间", "安排一下", "正式安排")):
            self._save_pending_confirmation(
                telegram_user_id=telegram_user_id,
                payload={
                    "kind": "awaiting_memo_schedule",
                    "candidate_task": candidate,
                },
            )
            return TelegramReply(
                chat_id=update.message.chat.id,
                text=f"好，我把“{candidate['title']}”从备忘里拎出来了。你想把它安排到什么时候？",
            )
        return None

    async def _maybe_request_write_confirmation(
        self,
        *,
        telegram_user_id: str,
        action: PlannedAction,
        execution_cache: TaskCommandExecutionCache | None = None,
    ) -> str | None:
        if self._session_factory is None or self._ticktick_client is None:
            return None
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None or not user.ticktick_access_token:
            return None
        if action.action_type not in {"create_task", "update_task", "complete_task"}:
            return None

        if execution_cache is not None and execution_cache.tasks is not None:
            tasks = execution_cache.tasks
        else:
            tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token, since=None)
            if execution_cache is not None:
                execution_cache.tasks = list(tasks)
        title = self._proposed_title(action) if action.action_type == "update_task" else self._action_title(action)
        target_lookup_title = self._target_lookup_title(action)
        start_at = self._coerce_datetime(action.payload.get("start_at"))
        due_at = self._coerce_datetime(action.payload.get("due_at"))
        end_at = self._coerce_datetime(action.payload.get("end_at"))
        duration_minutes = self._coerce_duration_minutes(action.payload.get("duration_minutes"))
        start_at, due_at, end_at = self._resolve_time_span(
            start_at=start_at,
            due_at=due_at,
            end_at=end_at,
            duration_minutes=duration_minutes,
        )

        if action.action_type in {"update_task", "complete_task"} and action.target_task_id is None:
            candidate_tasks = self._find_disambiguation_candidates(
                tasks=tasks,
                requested_title=target_lookup_title,
                exclude_task_id=action.target_task_id,
            )
            if len(candidate_tasks) > 1:
                rendered_candidates = [
                    {
                        "task_id": candidate.id,
                        "title": candidate.title,
                        "when": self._render_task_candidate_label(
                            task=candidate,
                            current_timezone=user.current_timezone or "America/Los_Angeles",
                        ),
                    }
                    for candidate in candidate_tasks[:5]
                ]
                self._save_pending_confirmation(
                    telegram_user_id=telegram_user_id,
                    payload={
                        "kind": "task_disambiguation",
                        "candidate_tasks": rendered_candidates,
                        "original_action": action.model_dump(),
                    },
                )
                return self._render_task_disambiguation_prompt(
                    requested_title=target_lookup_title or "这条任务",
                    candidates=rendered_candidates,
                )
            resolved_target = self._resolve_single_target_candidate(
                tasks=tasks,
                requested_title=target_lookup_title,
                exclude_task_id=action.target_task_id,
            )
            if resolved_target is not None:
                action.target_task_id = resolved_target.id

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
        if duplicate_task is not None and action.action_type == "update_task":
            self._save_pending_confirmation(
                telegram_user_id=telegram_user_id,
                payload={
                    "kind": "duplicate_update",
                    "candidate_task": {"task_id": duplicate_task.id, "title": duplicate_task.title},
                    "original_action": action.model_dump(),
                },
            )
            return (
                f"我看到一条和你要改成的“{title}”很像的未完成任务：{duplicate_task.title}。"
                "你想继续改名、合并到原来那条，还是改成别的标题？"
            )

        if start_at is not None or due_at is not None:
            conflict_task = self._find_conflicting_task(
                tasks=tasks,
                start_at=start_at,
                due_at=due_at,
                end_at=end_at,
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
        reply_text = await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        self._record_successful_action_memory(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        return reply_text

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
        reply_text = await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        self._record_successful_action_memory(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        return reply_text

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
        reply_text = await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        self._record_successful_action_memory(
            telegram_user_id=telegram_user_id,
            action=action,
            source_text=user_text,
        )
        return reply_text

    async def _execute_memo_schedule_candidate(
        self,
        *,
        telegram_user_id: str,
        user_text: str,
        candidate: dict,
    ) -> str:
        title = str(candidate.get("title") or "").strip()
        task_id = str(candidate.get("task_id") or "").strip()
        if not title or not task_id:
            return "我知道你想安排这条备忘，但这次没稳稳定位到具体对象。"
        current_timezone = "America/Los_Angeles"
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is not None:
            current_timezone = user.current_timezone or current_timezone
        planned = await self._planner.plan(
            self._context_builder.build(
                f"任务“{title}”安排到{user_text}",
                current_timezone=current_timezone,
            )
        )
        if not planned.actions:
            return f"我知道你想安排“{title}”，但新时间这次我还没抓稳。"
        action = planned.actions[0]
        action.action_type = "update_task"
        action.target_task_id = task_id
        action.payload.setdefault("match_title", title)
        action.payload.setdefault("semantic_type", "explicit_time")
        reply_text = await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        self._record_successful_action_memory(
            telegram_user_id=telegram_user_id,
            action=action,
            source_text=user_text,
        )
        return reply_text

    async def _execute_task_disambiguation_confirmation(
        self,
        *,
        telegram_user_id: str,
        context: dict,
        selected_index: int,
    ) -> str:
        candidate_tasks = context.get("candidate_tasks") or []
        if not (0 <= selected_index < len(candidate_tasks)):
            return "我知道你是在选任务，但这次没稳稳定位到你选的是哪一条。"
        action = PlannedAction.model_validate(context["original_action"])
        candidate = candidate_tasks[selected_index]
        action.target_task_id = str(candidate.get("task_id") or "")
        candidate_title = str(candidate.get("title") or "").strip()
        if action.action_type == "update_task" and candidate_title:
            action.payload.setdefault("match_title", candidate_title)
        if action.action_type == "complete_task" and candidate_title:
            action.payload["title"] = candidate_title
        reply_text = await self._task_command_service.execute_action(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        self._record_successful_action_memory(
            telegram_user_id=telegram_user_id,
            action=action,
        )
        self._record_disambiguation_memory(
            telegram_user_id=telegram_user_id,
            context=context,
            selected_index=selected_index,
        )
        return reply_text

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

    def _save_pending_batch(self, *, telegram_user_id: str, payload: dict) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.replace_active_context(
            user_id=user.id,
            context_type="pending_batch",
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

    def _load_pending_batch_context(self, *, telegram_user_id: str) -> dict | None:
        if self._memory_service is None:
            return None
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return None
        context = self._memory_service.get_active_context(user_id=user.id, context_type="pending_batch")
        return None if context is None else dict(context.payload_json or {})

    def _clear_pending_batch(self, *, telegram_user_id: str) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.clear_active_context(user_id=user.id, context_type="pending_batch")

    def _strip_pending_batch_selection_text(self, text: str) -> str:
        stripped = re.sub(r"^\s*第\s*[0-9一二三四五六七八九十]+\s*(?:条|个)?\s*", "", text)
        return stripped.strip()

    def _maybe_save_pending_query(
        self,
        *,
        telegram_user_id: str | None,
        reply_text: str,
        planned: PlannedConversation | None = None,
    ) -> None:
        if telegram_user_id is None:
            return
        if (
            planned is not None
            and planned.intent_type == "query"
            and planned.query is not None
            and self._looks_like_query_follow_up_prompt(reply_text)
        ):
            self._save_pending_query(
                telegram_user_id=telegram_user_id,
                payload={
                    "kind": "structured_query",
                    "query": planned.query.model_dump(mode="json"),
                },
            )
            return
        if self._looks_like_today_brief_prompt(reply_text):
            self._save_pending_query(
                telegram_user_id=telegram_user_id,
                payload={"kind": "today_brief"},
            )

    def _looks_like_today_brief_prompt(self, text: str) -> bool:
        lowered = text.casefold()
        return ("今天" in text or "今日" in text) and any(
            token in text or token in lowered
            for token in ("任务吗", "安排吗", "日程吗", "列出", "看看", "要我帮你", "帮你列")
        )

    def _looks_like_query_follow_up_prompt(self, text: str) -> bool:
        lowered = text.casefold()
        if not any(token in text or token in lowered for token in ("要我", "要不要", "帮你", "列出", "看看", "捋一遍")):
            return False
        return "吗" in text or "？" in text or "?" in text

    def _looks_like_pending_batch_continue(self, text: str) -> bool:
        lowered = text.casefold().strip()
        if lowered in {"继续", "继续吧", "继续呀", "继续啊", "对", "是", "好", "好的", "行", "可以", "嗯"}:
            return True
        return "继续" in text

    def _save_pending_query(self, *, telegram_user_id: str, payload: dict) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.replace_active_context(
            user_id=user.id,
            context_type="pending_query",
            payload_json=payload,
        )

    def _load_pending_query_context(self, *, telegram_user_id: str) -> dict | None:
        if self._memory_service is None:
            return None
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return None
        context = self._memory_service.get_active_context(user_id=user.id, context_type="pending_query")
        return None if context is None else dict(context.payload_json or {})

    def _clear_pending_query(self, *, telegram_user_id: str) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.clear_active_context(user_id=user.id, context_type="pending_query")

    async def _build_connected_today_brief_reply(
        self,
        *,
        chat_id: int,
        telegram_user_id: str,
    ) -> TelegramReply:
        if self._ticktick_oauth_service is not None:
            connected = await self._ticktick_oauth_service.has_connection(
                telegram_user_id=telegram_user_id
            )
            if not connected:
                return await self._build_ticktick_auth_reply(
                    chat_id=chat_id,
                    telegram_user_id=telegram_user_id,
                )
        if self._task_query_service is not None:
            return TelegramReply(
                chat_id=chat_id,
                text=await self._task_query_service.build_query_reply(
                    telegram_user_id=telegram_user_id,
                    query=PlannedQueryIntent(
                        query_kind="today_brief",
                        query_text="今天有什么安排",
                        time_scope="today",
                    ),
                ),
            )
        if self._today_brief_service is None:
            return TelegramReply(
                chat_id=chat_id,
                text="我知道你是在问今天要做什么，但今天简报这条链路现在还没完全接好。",
            )
        return TelegramReply(
            chat_id=chat_id,
            text=await self._today_brief_service.build_today_brief(
                telegram_user_id=telegram_user_id,
            ),
        )

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

    def _find_disambiguation_candidates(
        self,
        *,
        tasks: list,
        requested_title: str | None,
        exclude_task_id: str | None,
    ) -> list:
        if not requested_title:
            return []
        normalized_title = self._normalize_title(requested_title)
        open_tasks = [
            task
            for task in tasks
            if not task.completed and (task.status is None or task.status == 0) and (not exclude_task_id or task.id != exclude_task_id)
        ]
        exact_matches = [task for task in open_tasks if self._normalize_title(task.title) == normalized_title]
        if len(exact_matches) > 1:
            return exact_matches
        if len(exact_matches) == 1:
            return []
        fuzzy_matches = [
            task
            for task in open_tasks
            if normalized_title in self._normalize_title(task.title)
            or self._normalize_title(task.title) in normalized_title
            or self._duplicate_detector.is_probable_duplicate(task.title, requested_title)
        ]
        if len(fuzzy_matches) > 1:
            unique_matches: list = []
            seen_ids: set[str] = set()
            for task in fuzzy_matches:
                if task.id in seen_ids:
                    continue
                seen_ids.add(task.id)
                unique_matches.append(task)
            return unique_matches
        return []

    def _resolve_single_target_candidate(
        self,
        *,
        tasks: list,
        requested_title: str | None,
        exclude_task_id: str | None,
    ):
        candidates = self._find_disambiguation_candidates(
            tasks=tasks,
            requested_title=requested_title,
            exclude_task_id=exclude_task_id,
        )
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return None
        if not requested_title:
            return None
        normalized_title = self._normalize_title(requested_title)
        for task in tasks:
            if task.completed or task.status == 2:
                continue
            if exclude_task_id and task.id == exclude_task_id:
                continue
            if self._normalize_title(task.title) == normalized_title:
                return task
        return None

    def _find_conflicting_task(
        self,
        *,
        tasks: list,
        start_at: datetime | None,
        due_at: datetime | None,
        end_at: datetime | None,
        current_timezone: str,
        exclude_task_id: str | None,
    ) -> object | None:
        proposed_start = start_at or due_at
        proposed_end = end_at or due_at
        if proposed_start is None:
            return None
        for task in tasks:
            if task.completed or task.status == 2:
                continue
            if exclude_task_id and task.id == exclude_task_id:
                continue
            task_start_at = self._parse_ticktick_datetime(task.startDate or task.dueDate, timezone_name=current_timezone)
            task_end_at = self._parse_ticktick_datetime(task.dueDate or task.startDate, timezone_name=current_timezone)
            if task_start_at is None:
                continue
            if self._conflict_detector.has_conflict(
                proposed_start.isoformat(),
                task_start_at.isoformat(),
                end_iso=proposed_end.isoformat() if proposed_end is not None else None,
                other_end_iso=task_end_at.isoformat() if task_end_at is not None else None,
            ):
                return task
        return None

    def _render_task_disambiguation_prompt(self, *, requested_title: str, candidates: list[dict]) -> str:
        lines = [f"我找到不止一条和“{requested_title}”对应的未完成任务。你是指哪一条？"]
        for index, candidate in enumerate(candidates, start=1):
            label = str(candidate.get("when") or candidate.get("title") or "").strip()
            title = str(candidate.get("title") or "").strip()
            if label and title:
                lines.append(f"{index}. {label} {title}".strip())
            elif title:
                lines.append(f"{index}. {title}")
        lines.append("你直接回我“第一条”“第二条”或者“最后一个”就行。")
        return "\n".join(lines)

    def _render_task_candidate_label(self, *, task, current_timezone: str) -> str:
        when = self._parse_ticktick_datetime(task.startDate or task.dueDate, timezone_name=current_timezone)
        if when is None:
            return ""
        return f"{self._message_renderer.render_weekday(when)} {when.strftime('%H:%M')}"

    def _extract_candidate_index(self, *, text: str, candidate_count: int) -> int | None:
        if candidate_count <= 0:
            return None
        if "最后" in text:
            return candidate_count - 1
        match = re.search(r"第\s*(\d+)\s*[条项个件]?", text)
        if match:
            index = int(match.group(1)) - 1
            return index if 0 <= index < candidate_count else None
        chinese_ordinals = {
            "第一": 0,
            "第二": 1,
            "第三": 2,
            "第四": 3,
            "第五": 4,
        }
        for token, index in chinese_ordinals.items():
            if token in text and index < candidate_count:
                return index
        return 0 if candidate_count == 1 else None

    def _extract_schedule_phrase(self, *, text: str) -> str | None:
        match = re.search(r"(?:安排到|安排成|改到|改成)(.+?)(?:$|，|,|。)", text)
        if match:
            return match.group(1).strip()
        return None

    def _coerce_duration_minutes(self, value: object | None) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if not isinstance(value, str):
            return None
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None

    def _resolve_time_span(
        self,
        *,
        start_at: datetime | None,
        due_at: datetime | None,
        end_at: datetime | None,
        duration_minutes: int | None,
    ) -> tuple[datetime | None, datetime | None, datetime | None]:
        resolved_end_at = end_at
        if resolved_end_at is None and start_at is not None and duration_minutes is not None:
            resolved_end_at = start_at + timedelta(minutes=duration_minutes)
        resolved_due_at = due_at
        if resolved_due_at is None and resolved_end_at is not None:
            resolved_due_at = resolved_end_at
        return start_at, resolved_due_at, resolved_end_at

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

    def _clean_optional_text(self, value: object | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _record_successful_action_memory(
        self,
        *,
        telegram_user_id: str,
        action: PlannedAction,
        source_text: str | None = None,
    ) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        now = datetime.now(timezone.utc)
        self._record_time_expression_memory(
            user_id=user.id,
            action=action,
            source_text=source_text,
            last_confirmed_at=now,
        )
        self._record_alias_mappings_for_action(
            user_id=user.id,
            action=action,
            last_confirmed_at=now,
        )

    def _record_disambiguation_memory(
        self,
        *,
        telegram_user_id: str,
        context: dict,
        selected_index: int,
    ) -> None:
        if self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        candidate_tasks = context.get("candidate_tasks") or []
        if not (0 <= selected_index < len(candidate_tasks)):
            return
        original_action = PlannedAction.model_validate(context["original_action"])
        requested_title = self._target_lookup_title(original_action)
        if not requested_title:
            return
        candidate = candidate_tasks[selected_index]
        selected_title = str(candidate.get("title") or "").strip()
        if not selected_title:
            return
        candidate_titles = [
            str(item.get("title") or "").strip()
            for item in candidate_tasks
            if str(item.get("title") or "").strip()
        ]
        self._memory_service.record_disambiguation_pattern(
            user_id=user.id,
            key=requested_title,
            selected_title=selected_title,
            selected_task_id=str(candidate.get("task_id") or "").strip() or None,
            selected_when=str(candidate.get("when") or "").strip() or None,
            candidate_titles=candidate_titles or None,
            selection_index=selected_index + 1,
            source_type="user_confirmation",
            last_confirmed_at=datetime.now(timezone.utc),
        )

    def _record_time_expression_memory(
        self,
        *,
        user_id: int,
        action: PlannedAction,
        source_text: str | None,
        last_confirmed_at: datetime,
    ) -> None:
        raw_nl_time = self._clean_optional_text(action.payload.get("raw_nl_time"))
        if raw_nl_time is None and source_text is not None:
            raw_nl_time = self._extract_time_expression_text(source_text)
        if raw_nl_time is None:
            return
        start_at = self._coerce_datetime(action.payload.get("start_at"))
        due_at = self._coerce_datetime(action.payload.get("due_at"))
        end_at = self._coerce_datetime(action.payload.get("end_at"))
        duration_minutes = self._coerce_duration_minutes(action.payload.get("duration_minutes"))
        start_at, due_at, end_at = self._resolve_time_span(
            start_at=start_at,
            due_at=due_at,
            end_at=end_at,
            duration_minutes=duration_minutes,
        )
        window_start = self._coerce_datetime(action.payload.get("window_start"))
        window_end = self._coerce_datetime(action.payload.get("window_end"))
        if due_at is None and start_at is None and end_at is None and window_start is None and window_end is None:
            return
        self._memory_service.record_time_expression(
            user_id=user_id,
            raw_nl_time=raw_nl_time,
            resolved_due_at=due_at,
            resolved_window_start=window_start,
            resolved_window_end=window_end,
            semantic_type=self._clean_optional_text(action.payload.get("semantic_type")),
            source_type="task_execution",
            last_confirmed_at=last_confirmed_at,
        )

    def _record_alias_mappings_for_action(
        self,
        *,
        user_id: int,
        action: PlannedAction,
        last_confirmed_at: datetime,
    ) -> None:
        if action.action_type not in {"create_task", "update_task"}:
            return
        list_name = self._clean_optional_text(action.payload.get("list_name"))
        tags = self._normalize_text_list(action.payload.get("tags"))
        if not list_name and not tags:
            return

        canonical_list_name = list_name
        canonical_tags = list(tags)
        if action.target_task_id:
            shadow = self._load_task_shadow(user_id=user_id, task_id=action.target_task_id)
            if shadow is not None:
                if shadow.list_name:
                    canonical_list_name = shadow.list_name
                if shadow.tags_json:
                    canonical_tags = [
                        str(item).strip()
                        for item in shadow.tags_json
                        if str(item).strip()
                    ] or canonical_tags

        if list_name and canonical_list_name:
            self._memory_service.record_alias_mapping(
                user_id=user_id,
                alias=list_name,
                canonical_name=canonical_list_name,
                kind="list",
                source_type="task_execution",
                last_confirmed_at=last_confirmed_at,
            )

        for tag in tags:
            canonical_tag = next(
                (candidate for candidate in canonical_tags if candidate.casefold() == tag.casefold()),
                tag,
            )
            self._memory_service.record_alias_mapping(
                user_id=user_id,
                alias=tag,
                canonical_name=canonical_tag,
                kind="tag",
                source_type="task_execution",
                last_confirmed_at=last_confirmed_at,
            )

    def _load_task_shadow(self, *, user_id: int, task_id: str) -> TaskShadow | None:
        if self._session_factory is None:
            return None
        with self._session_factory() as session:
            return (
                session.query(TaskShadow)
                .filter(TaskShadow.user_id == user_id, TaskShadow.ticktick_task_id == task_id)
                .one_or_none()
            )

    def _normalize_text_list(self, value: object | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        elif isinstance(value, str):
            raw_items = [part.strip() for part in value.split(",")]
        else:
            raw_items = [value]
        cleaned_items: list[str] = []
        for item in raw_items:
            text = str(item).strip()
            if text and text not in cleaned_items:
                cleaned_items.append(text)
        return cleaned_items

    def _build_conversation_summary(self, text: str) -> str:
        cleaned = " ".join(text.strip().split())
        if not cleaned:
            return "空消息"
        if self._looks_like_today_brief_request(cleaned):
            return "查询今天安排"
        list_alias = self._extract_list_alias(cleaned)
        if list_alias and any(token in cleaned for token in ("放到", "移到", "加到", "放进", "加入")):
            return f"移动到 list：{list_alias}"
        if any(token in cleaned for token in ("做完", "完成", "勾掉", "勾选")):
            return f"完成任务：{self._strip_summary_filler(cleaned)[:80]}"
        if any(token in cleaned for token in ("改到", "改成", "挪到", "推到", "提前", "延后", "补一句", "放到")):
            return f"修改任务：{self._strip_summary_filler(cleaned)[:80]}"
        if any(token in cleaned for token in ("提醒", "记一下", "记住", "新增", "新建", "添加")):
            time_phrase = self._extract_time_expression_text(cleaned)
            body = self._strip_summary_filler(cleaned)
            if time_phrase and time_phrase in body:
                body = body.replace(time_phrase, "").strip(" ，,。；;")
                if body:
                    return f"提醒：{time_phrase} {body[:60]}"
                return f"提醒：{time_phrase}"
            return f"提醒：{body[:80]}"
        return self._strip_summary_filler(cleaned)[:120]

    def _strip_summary_filler(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^(请|麻烦)?帮我把?\s*", "", cleaned)
        cleaned = re.sub(r"^(请|麻烦)?帮我\s*", "", cleaned)
        cleaned = re.sub(r"^提醒我\s*", "", cleaned)
        cleaned = re.sub(r"^(记一下|记住|安排一下|安排|新建|新增|添加|设置)\s*", "", cleaned)
        cleaned = cleaned.replace("那个 list", "list")
        cleaned = cleaned.replace("这个 list", "list")
        cleaned = cleaned.replace("那个", "")
        cleaned = cleaned.replace("这个", "")
        return cleaned.strip(" ，,。；;")

    def _extract_list_alias(self, text: str) -> str | None:
        match = re.search(r"(?:放到|移到|加到|放进|加入)\s*(.+?)(?:那个|这个)?\s*(?:list|清单|文件夹|tag|标签)", text)
        if not match:
            return None
        return self._strip_summary_filler(match.group(1))

    def _extract_time_expression_text(self, text: str) -> str | None:
        match = re.search(
            r"((?:今天|明天|后天|今晚|明早|明晚|下周[一二三四五六日天]?|这周[一二三四五六日天]?|本周[一二三四五六日天]?|月底前|这两周|周末|工作日|周[一二三四五六日天])"
            r"(?:\s*(?:上午|下午|中午|晚上|凌晨)?)?"
            r"(?:\s*\d{1,2}(?:[:：]\d{2})?(?:点|时)?(?:半|一刻|三刻)?)?)",
            text,
        )
        if not match:
            return None
        return match.group(1).strip()

    def _record_turn_summary(self, *, telegram_user_id: str | None, text: str) -> None:
        if telegram_user_id is None or self._memory_service is None:
            return
        user = self._get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return
        self._memory_service.save_conversation_summary(
            user_id=user.id,
            summary_text=self._build_conversation_summary(text),
        )
        self._memory_service.upsert_fact(
            user_id=user.id,
            key="default_language",
            value_json={"value": user.default_language or "zh-CN"},
            memory_type=MemoryType.PREFERENCE,
            source_type="system",
        )

    def _write_action_log(
        self,
        *,
        update: TelegramUpdate,
        trace: dict[str, object],
        replies: list[TelegramReply],
    ) -> None:
        if self._session_factory is None or update.message is None or update.message.text is None:
            return
        telegram_user_id = self._telegram_user_id(update)
        if telegram_user_id is None:
            return
        reply_texts = [reply.text for reply in replies]
        execution_result_json = trace.get("execution_result_json")
        if not isinstance(execution_result_json, dict):
            execution_result_json = {"reply_texts": reply_texts}
        elif reply_texts and "reply_texts" not in execution_result_json:
            execution_result_json = {**execution_result_json, "reply_texts": reply_texts}
        status = str(trace.get("status") or "completed")
        parsed_plan_json = trace.get("parsed_plan_json")
        with self._session_factory() as session:
            user = UserRepository(session).get_or_create(telegram_user_id=telegram_user_id)
            ActionLogRepository(session).add(
                ActionLog(
                    user_id=user.id,
                    source_message_id=str(update.message.message_id),
                    original_text=update.message.text,
                    parsed_plan_json=parsed_plan_json if isinstance(parsed_plan_json, dict) else None,
                    execution_result_json=execution_result_json,
                    status=status,
                )
            )
            session.commit()


class NoopConversationService(ConversationService):
    def __init__(self) -> None:
        super().__init__(context_builder=ContextBuilder(), planner=OpenAIPlanner(client=None))

    async def handle_update(self, update: TelegramUpdate) -> list[TelegramReply]:
        return []
