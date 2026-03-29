from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
import math
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.schemas import PlannedAction
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickTask
from ticktick_telegram_assistant.repositories.reminders import ReminderRepository
from ticktick_telegram_assistant.repositories.task_shadows import TaskShadowRepository
from ticktick_telegram_assistant.repositories.users import UserRepository
from ticktick_telegram_assistant.services.briefing_service import BriefingService
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer
from ticktick_telegram_assistant.services.task_command_service import TaskCommandService
from ticktick_telegram_assistant.services.ticktick_snapshot_service import TickTickSnapshotService
from ticktick_telegram_assistant.services.today_brief_service import TodayBriefService


logger = logging.getLogger(__name__)


class ReminderWorker:
    _MORNING_BRIEF_CATCHUP = timedelta(hours=4)
    _EVENING_REVIEW_CATCHUP = timedelta(hours=3)
    _WEEKLY_MEMO_CLEANUP_CATCHUP = timedelta(hours=10)
    _PENDING_RETRY_BASE = timedelta(minutes=1)
    _PENDING_RETRY_MAX = timedelta(minutes=15)
    _MORNING_BRIEF_PENDING_TTL = timedelta(hours=10)
    _EVENING_REVIEW_PENDING_TTL = timedelta(hours=8)
    _PRESTART_MISSED_GRACE = timedelta(minutes=10)

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None = None,
        ticktick_client=None,
        telegram_client=None,
        briefing_service: BriefingService | None = None,
        renderer: MessageRenderer | None = None,
        today_brief_service: TodayBriefService | None = None,
        task_command_service: TaskCommandService | None = None,
        snapshot_service: TickTickSnapshotService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client
        self._telegram_client = telegram_client
        self._briefing_service = briefing_service or BriefingService()
        self._renderer = renderer or MessageRenderer()
        self._snapshot_service = snapshot_service or (
            TickTickSnapshotService(session_factory=session_factory, ticktick_client=ticktick_client)
            if session_factory is not None
            else None
        )
        self._today_brief_service = today_brief_service or (
            TodayBriefService(
                session_factory=session_factory,
                ticktick_client=ticktick_client,
                briefing_service=self._briefing_service,
                renderer=self._renderer,
                snapshot_service=self._snapshot_service,
            )
            if session_factory is not None and ticktick_client is not None
            else None
        )
        self._task_command_service = task_command_service

    async def run_once(self, *, now: datetime | None = None) -> None:
        if self._session_factory is None or self._ticktick_client is None or self._telegram_client is None:
            return None

        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        with self._session_factory() as session:
            users = UserRepository(session).list_connected_users()

        for user in users:
            await self._process_user(user=user, now=current_time)

    async def _process_user(self, *, user: User, now: datetime) -> None:
        timezone_name = user.current_timezone or "America/Los_Angeles"
        local_now = now.astimezone(ZoneInfo(timezone_name))
        await self._send_due_pending_events(user=user, now=now)
        tasks, tasks_fetch_error, tasks_are_stale, snapshot_synced_at = await self._try_list_tasks(user=user, now=now)
        task_lines = self._build_task_lines(tasks=tasks or [], timezone_name=timezone_name)

        morning_schedule = self._resolve_morning_brief_schedule(local_now=local_now)
        if morning_schedule is not None and self._today_brief_service is not None:
            dedupe_key = f"morning_brief:{morning_schedule.date().isoformat()}"
            if tasks is None:
                if self._is_retryable_ticktick_error(tasks_fetch_error):
                    await self._queue_generated_pending_event(
                        user=user,
                        event_type="morning_brief",
                        dedupe_key=dedupe_key,
                        scheduled_at=morning_schedule,
                        payload_json={
                            "timezone_name": timezone_name,
                            "attempt_count": 0,
                            "expires_at": (morning_schedule + self._MORNING_BRIEF_PENDING_TTL).isoformat(),
                        },
                    )
            else:
                text = await self._today_brief_service.build_today_brief_for_user(
                    user=user,
                    now=local_now,
                    tasks=tasks,
                    snapshot_is_stale=tasks_are_stale,
                    snapshot_synced_at=snapshot_synced_at,
                )
                if text:
                    await self._send_once(
                        user=user,
                        dedupe_key=dedupe_key,
                        event_type="morning_brief",
                        scheduled_at=morning_schedule,
                        text=text,
                        now=now,
                    )

        if tasks is not None:
            await self._send_prestart_reminders(
                user=user,
                local_now=local_now,
                tasks=tasks,
                timezone_name=timezone_name,
                now=now,
            )
        await self._send_windowed_reminders(user=user, local_now=local_now, now=now)
        await self._send_weekly_memo_cleanup(user=user, local_now=local_now, now=now)

        evening_schedule = self._resolve_evening_review_schedule(local_now=local_now)
        if evening_schedule is not None:
            scheduled_at, review_date = evening_schedule
            dedupe_key = f"evening_review:{review_date.isoformat()}"
            if tasks is None:
                if self._is_retryable_ticktick_error(tasks_fetch_error):
                    await self._queue_generated_pending_event(
                        user=user,
                        event_type="evening_review",
                        dedupe_key=dedupe_key,
                        scheduled_at=scheduled_at,
                        payload_json={
                            "timezone_name": timezone_name,
                            "review_date": review_date.isoformat(),
                            "attempt_count": 0,
                            "expires_at": (scheduled_at + self._EVENING_REVIEW_PENDING_TTL).isoformat(),
                        },
                    )
                return
            candidates = self._build_evening_review_candidates(
                review_date=review_date,
                task_lines=task_lines,
            )
            if review_date != local_now.date() and not candidates:
                return
            text = self._build_evening_review(review_date=review_date, task_lines=task_lines)
            if text:
                await self._send_once(
                    user=user,
                    dedupe_key=dedupe_key,
                    event_type="evening_review",
                    scheduled_at=scheduled_at,
                    text=text,
                    payload_json={
                        "text": text,
                        "candidate_tasks": candidates,
                        "review_date": review_date.isoformat(),
                    },
                    now=now,
                )

    async def _send_prestart_reminders(
        self,
        *,
        user: User,
        local_now: datetime,
        tasks: list[TickTickTask],
        timezone_name: str,
        now: datetime,
    ) -> None:
        for task in tasks:
            if task.status == 2 or task.completed or task.isAllDay:
                continue
            anchor_at = self._task_anchor_datetime(task=task, timezone_name=timezone_name)
            if anchor_at is None:
                continue
            scheduled_at = anchor_at - timedelta(minutes=5)
            is_on_time = scheduled_at <= local_now < anchor_at
            is_catch_up = anchor_at <= local_now < anchor_at + self._PRESTART_MISSED_GRACE
            if not (is_on_time or is_catch_up):
                continue
            description = task.desc or task.content or None
            if is_catch_up:
                text = f"刚刚错过 5 分钟提醒，不过现在也该留意了：{self._renderer.render_weekday(anchor_at)} {anchor_at.strftime('%H:%M')} {task.title}"
            else:
                text = f"还有 5 分钟：{self._renderer.render_weekday(anchor_at)} {anchor_at.strftime('%H:%M')} {task.title}"
            if description:
                text = f"{text}，{description}"
            await self._send_once(
                user=user,
                dedupe_key=f"prestart:{task.id}:{scheduled_at.isoformat()}",
                event_type="prestart_reminder",
                scheduled_at=scheduled_at,
                text=text,
                now=now,
                ticktick_task_id=task.id,
                payload_json={
                    "text": text,
                    "task_id": task.id,
                    "title": task.title,
                    "task_due_at": scheduled_at.isoformat(),
                    "task_deadline_at": task.dueDate,
                },
            )

    def _build_evening_review(self, *, review_date: date, task_lines: list[dict]) -> str | None:
        review_items = [item for item in task_lines if item["date"] == review_date.isoformat()]
        review_items.sort(key=lambda item: item["sort_key"])
        if not review_items:
            return "今天快结束啦。我这边没看到明确落在今天的安排；如果有想顺手收尾的事，也可以直接跟我说。"

        lines = ["一天快收尾啦。今天这些事哪些已经做完了？"]
        for item in review_items[:8]:
            lines.append(f"- {self._renderer.render_task_line(item)}")
        lines.append("你直接回我“前两个做完了”或者“第三个改到明天下午”就行。")
        return "\n".join(lines)

    async def _send_once(
        self,
        *,
        user: User,
        dedupe_key: str,
        event_type: str,
        scheduled_at: datetime,
        text: str,
        now: datetime,
        ticktick_task_id: str | None = None,
        payload_json: dict | None = None,
    ) -> None:
        with self._session_factory() as session:
            reminder_repo = ReminderRepository(session)
            if reminder_repo.get_by_dedupe_key(dedupe_key) is not None:
                return

        await self._telegram_client.send_message(chat_id=int(user.telegram_user_id), text=text)

        with self._session_factory() as session:
            reminder_repo = ReminderRepository(session)
            if reminder_repo.get_by_dedupe_key(dedupe_key) is not None:
                return
            event = reminder_repo.add(
                ReminderEvent(
                    user_id=user.id,
                    ticktick_task_id=ticktick_task_id,
                    event_type=event_type,
                    scheduled_at=scheduled_at,
                    dedupe_key=dedupe_key,
                    payload_json=payload_json or {"text": text},
                    status="sent",
                    sent_at=now,
                )
            )
            session.commit()
            reminder_repo.mark_sent(event, sent_at=now)

    async def _send_due_pending_events(self, *, user: User, now: datetime) -> None:
        with self._session_factory() as session:
            reminder_repo = ReminderRepository(session)
            pending_events = reminder_repo.list_due_pending_for_user(user_id=user.id, now=now)
            for event in pending_events:
                try:
                    text, payload_json = await self._build_pending_event_delivery(user=user, event=event)
                except Exception as exc:
                    if self._pending_event_expired(event=event, now=now):
                        reminder_repo.mark_status(event, status="expired")
                    else:
                        pending_payload = dict(event.payload_json or {})
                        attempts = int(pending_payload.get("attempt_count", 0))
                        pending_payload["attempt_count"] = attempts + 1
                        reminder_repo.reschedule_pending(
                            event,
                            scheduled_at=now + self._pending_retry_delay(attempts),
                            payload_json=pending_payload,
                        )
                    logger.warning("pending reminder delivery failed for %s", event.dedupe_key, exc_info=exc)
                    continue
                if not text:
                    reminder_repo.mark_status(event, status="expired")
                    continue
                await self._telegram_client.send_message(chat_id=int(user.telegram_user_id), text=text)
                event.payload_json = payload_json
                reminder_repo.mark_sent(event, sent_at=now)
            session.commit()

    async def _try_list_tasks(
        self,
        *,
        user: User,
        now: datetime,
    ) -> tuple[list[TickTickTask] | None, Exception | None, bool, datetime | None]:
        try:
            if self._snapshot_service is not None:
                result = await self._snapshot_service.list_tasks_for_user(user=user, now=now)
                tasks = result.items
                return tasks, None, result.is_stale, result.snapshot_synced_at
            tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token, since=None)
        except Exception as exc:
            logger.warning("ticktick list_tasks failed for user %s", user.telegram_user_id, exc_info=exc)
            return None, exc, False, None
        return tasks, None, False, None

    def _is_retryable_ticktick_error(self, error: Exception | None) -> bool:
        if error is None:
            return False
        if isinstance(error, httpx.RequestError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            return 500 <= error.response.status_code < 600
        return False

    async def _queue_generated_pending_event(
        self,
        *,
        user: User,
        event_type: str,
        dedupe_key: str,
        scheduled_at: datetime,
        payload_json: dict,
    ) -> None:
        with self._session_factory() as session:
            reminder_repo = ReminderRepository(session)
            if reminder_repo.get_by_dedupe_key(dedupe_key) is not None:
                return
            reminder_repo.add(
                ReminderEvent(
                    user_id=user.id,
                    event_type=event_type,
                    scheduled_at=scheduled_at,
                    status="pending",
                    payload_json=payload_json,
                    dedupe_key=dedupe_key,
                )
            )
            session.commit()

    async def _build_pending_event_delivery(
        self,
        *,
        user: User,
        event: ReminderEvent,
    ) -> tuple[str | None, dict]:
        payload_json = dict(event.payload_json or {})
        text = payload_json.get("text")
        if text:
            return str(text), payload_json

        timezone_name = payload_json.get("timezone_name") or user.current_timezone or "America/Los_Angeles"
        if event.event_type == "morning_brief" and self._today_brief_service is not None:
            local_now = event.scheduled_at.astimezone(ZoneInfo(timezone_name))
            text = await self._today_brief_service.build_today_brief_for_user(user=user, now=local_now)
            payload_json["text"] = text
            return text, payload_json

        if event.event_type == "evening_review":
            tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token, since=None)
            task_lines = self._build_task_lines(tasks=tasks, timezone_name=timezone_name)
            review_date_raw = payload_json.get("review_date")
            review_date = date.fromisoformat(review_date_raw) if review_date_raw else event.scheduled_at.date()
            text = self._build_evening_review(review_date=review_date, task_lines=task_lines)
            payload_json["text"] = text
            payload_json["candidate_tasks"] = self._build_evening_review_candidates(
                review_date=review_date,
                task_lines=task_lines,
            )
            return text, payload_json

        if event.event_type == "ticktick_write_retry" and self._task_command_service is not None:
            action_payload = payload_json.get("action") or {}
            source_text = payload_json.get("source_text")
            action = PlannedAction.model_validate(action_payload)
            reply_text = await self._task_command_service.execute_action(
                telegram_user_id=user.telegram_user_id,
                action=action,
                source_text=source_text if isinstance(source_text, str) else None,
                allow_retry_queue=False,
            )
            payload_json["text"] = reply_text
            return f"刚才卡住的那条我现在已经补上了：\n{reply_text}", payload_json

        return payload_json.get("text"), payload_json

    def _pending_event_expired(self, *, event: ReminderEvent, now: datetime) -> bool:
        payload_json = event.payload_json or {}
        raw_expiry = payload_json.get("expires_at")
        if not raw_expiry:
            return False
        try:
            expires_at = datetime.fromisoformat(str(raw_expiry))
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=now.tzinfo)
        return now >= expires_at

    def _pending_retry_delay(self, attempts: int) -> timedelta:
        delay_seconds = self._PENDING_RETRY_BASE.total_seconds() * (2**attempts)
        return timedelta(seconds=min(delay_seconds, self._PENDING_RETRY_MAX.total_seconds()))

    async def _send_windowed_reminders(self, *, user: User, local_now: datetime, now: datetime) -> None:
        with self._session_factory() as session:
            shadows = TaskShadowRepository(session).list_windowed_by_user(user_id=user.id)

        for shadow in shadows:
            checkpoints = self._windowed_checkpoints(shadow=shadow, timezone_name=user.current_timezone or "America/Los_Angeles")
            for event_type, scheduled_at in checkpoints:
                if scheduled_at is None or not self._is_scheduled_window(local_now=local_now, scheduled_at=scheduled_at):
                    continue
                title = shadow.normalized_title or "待推进事项"
                raw_nl_time = shadow.raw_nl_time or "这段时间"
                text = f"轻轻提醒你一下：{raw_nl_time} 这段时间里，记得推进「{title}」。"
                await self._send_once(
                    user=user,
                    dedupe_key=f"{event_type}:{shadow.ticktick_task_id}:{scheduled_at.isoformat()}",
                    event_type=event_type,
                    scheduled_at=scheduled_at,
                    text=text,
                    now=now,
                    ticktick_task_id=shadow.ticktick_task_id,
                    payload_json={"text": text, "task_id": shadow.ticktick_task_id, "title": title},
                )

    async def _send_weekly_memo_cleanup(self, *, user: User, local_now: datetime, now: datetime) -> None:
        scheduled_at = self._resolve_weekly_memo_cleanup_schedule(local_now=local_now)
        if scheduled_at is None:
            return

        with self._session_factory() as session:
            memos = TaskShadowRepository(session).list_memo_by_user(user_id=user.id)

        memo_items = []
        for shadow in memos[:8]:
            title = shadow.normalized_title or "待整理备忘"
            memo_items.append(
                {
                    "task_id": shadow.ticktick_task_id,
                    "title": title,
                    "description": shadow.raw_nl_time,
                }
            )

        if not memo_items:
            return

        lines = ["周末收尾一下：这周攒下的备忘先过一遍。"]
        for item in memo_items:
            if item["description"]:
                lines.append(f"- {item['title']}（{item['description']}）")
            else:
                lines.append(f"- {item['title']}")
        if len(memos) > len(memo_items):
            lines.append(f"还有 {len(memos) - len(memo_items)} 条我先没展开。")
        lines.append("你可以直接回我“把第1条变成任务”或者“先都留着”。")
        text = "\n".join(lines)
        week = scheduled_at.date().isocalendar()
        dedupe_key = f"memo_cleanup:{user.id}:{week.year}-W{week.week:02d}"
        await self._send_once(
            user=user,
            dedupe_key=dedupe_key,
            event_type="memo_cleanup",
            scheduled_at=scheduled_at,
            text=text,
            now=now,
            payload_json={
                "text": text,
                "candidate_tasks": memo_items,
            },
        )

    def _build_evening_review_candidates(self, *, review_date: date, task_lines: list[dict]) -> list[dict[str, str]]:
        review_items = [item for item in task_lines if item["date"] == review_date.isoformat()]
        review_items.sort(key=lambda item: item["sort_key"])
        return [
            {"task_id": str(item["task_id"]), "title": str(item["title"])}
            for item in review_items[:8]
            if item.get("task_id") and item.get("title")
        ]

    def _build_task_lines(self, *, tasks: list[TickTickTask], timezone_name: str) -> list[dict]:
        lines: list[dict] = []
        for task in tasks:
            if task.status == 2 or task.completed:
                continue
            start_dt = self._parse_ticktick_datetime(task.startDate, timezone_name=timezone_name)
            due_dt = self._parse_ticktick_datetime(task.dueDate, timezone_name=timezone_name)
            effective_dt = start_dt or due_dt
            if effective_dt is None:
                continue
            if task.isAllDay:
                when = "今天"
            elif start_dt is not None and due_dt is not None and due_dt > start_dt:
                when = f"{start_dt.strftime('%H:%M')}-{due_dt.strftime('%H:%M')}"
            else:
                when = effective_dt.strftime("%H:%M")
            lines.append(
                {
                    "task_id": task.id,
                    "date": effective_dt.date().isoformat(),
                    "sort_key": effective_dt,
                    "weekday": self._renderer.render_weekday(effective_dt),
                    "when": when,
                    "title": task.title,
                    "description": task.desc or task.content or None,
                }
            )
        return lines

    def _task_anchor_datetime(self, *, task: TickTickTask, timezone_name: str) -> datetime | None:
        anchor_raw = task.startDate or task.dueDate
        return self._parse_ticktick_datetime(anchor_raw, timezone_name=timezone_name)

    def _parse_ticktick_datetime(self, raw: str | None, *, timezone_name: str) -> datetime | None:
        if not raw:
            return None
        formats = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")
        for fmt in formats:
            try:
                parsed = datetime.strptime(raw, fmt)
                return parsed.astimezone(ZoneInfo(timezone_name))
            except ValueError:
                continue
        return None

    def _is_trigger_time(self, current_time: datetime, *, hour: int, minute: int, grace_seconds: int = 60) -> bool:
        scheduled = current_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = (current_time - scheduled).total_seconds()
        return 0 <= delta < grace_seconds

    def _resolve_morning_brief_schedule(self, *, local_now: datetime) -> datetime | None:
        scheduled = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
        if scheduled <= local_now < scheduled + self._MORNING_BRIEF_CATCHUP:
            return scheduled
        return None

    def _resolve_evening_review_schedule(self, *, local_now: datetime) -> tuple[datetime, date] | None:
        same_day_scheduled = local_now.replace(hour=23, minute=30, second=0, microsecond=0)
        if same_day_scheduled <= local_now < same_day_scheduled + self._EVENING_REVIEW_CATCHUP:
            return same_day_scheduled, local_now.date()

        if local_now.timetz().replace(tzinfo=None) >= time(2, 30):
            return None

        previous_date = local_now.date() - timedelta(days=1)
        previous_scheduled = datetime.combine(previous_date, time(23, 30), tzinfo=local_now.tzinfo)
        if previous_scheduled <= local_now < previous_scheduled + self._EVENING_REVIEW_CATCHUP:
            return previous_scheduled, previous_date
        return None

    def _resolve_weekly_memo_cleanup_schedule(self, *, local_now: datetime) -> datetime | None:
        same_day_scheduled = local_now.replace(hour=17, minute=0, second=0, microsecond=0)
        if local_now.weekday() == 6 and same_day_scheduled <= local_now < same_day_scheduled + self._WEEKLY_MEMO_CLEANUP_CATCHUP:
            return same_day_scheduled

        if local_now.weekday() != 0 or local_now.timetz().replace(tzinfo=None) >= time(3, 0):
            return None

        previous_date = local_now.date() - timedelta(days=1)
        previous_scheduled = datetime.combine(previous_date, time(17, 0), tzinfo=local_now.tzinfo)
        if previous_scheduled <= local_now < previous_scheduled + self._WEEKLY_MEMO_CLEANUP_CATCHUP:
            return previous_scheduled
        return None

    def _windowed_checkpoints(self, *, shadow, timezone_name: str) -> list[tuple[str, datetime | None]]:
        start_at = self._normalize_shadow_datetime(shadow.window_start, timezone_name=timezone_name)
        end_at = self._normalize_shadow_datetime(shadow.window_end, timezone_name=timezone_name)
        if start_at is None or end_at is None:
            return []
        inclusive_days = (end_at.date() - start_at.date()).days + 1
        start_ping = start_at.replace(hour=0, minute=0, second=0, microsecond=0)
        if inclusive_days <= 2:
            return [("window_start_ping", start_ping)]

        deadline_ping = (end_at - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if inclusive_days <= 4:
            return [
                ("window_start_ping", start_ping),
                ("window_deadline_ping", deadline_ping),
            ]

        midpoint_day_offset = max(0, math.ceil(inclusive_days / 2))
        midpoint = start_at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=midpoint_day_offset)
        return [
            ("window_start_ping", start_ping),
            ("window_midpoint_ping", midpoint),
            ("window_deadline_ping", deadline_ping),
        ]

    def _normalize_shadow_datetime(self, value: datetime | None, *, timezone_name: str) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo(timezone_name))
        return value.astimezone(ZoneInfo(timezone_name))

    def _is_scheduled_window(self, *, local_now: datetime, scheduled_at: datetime, grace_seconds: int = 60) -> bool:
        return local_now.date() == scheduled_at.date()
