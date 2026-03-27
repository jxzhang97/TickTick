from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.reminder_event import ReminderEvent
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickTask
from ticktick_telegram_assistant.repositories.reminders import ReminderRepository
from ticktick_telegram_assistant.repositories.task_shadows import TaskShadowRepository
from ticktick_telegram_assistant.repositories.users import UserRepository
from ticktick_telegram_assistant.services.briefing_service import BriefingService
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


class ReminderWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None = None,
        ticktick_client=None,
        telegram_client=None,
        briefing_service: BriefingService | None = None,
        renderer: MessageRenderer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client
        self._telegram_client = telegram_client
        self._briefing_service = briefing_service or BriefingService()
        self._renderer = renderer or MessageRenderer()

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
        tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token, since=None)
        task_lines = self._build_task_lines(tasks=tasks, timezone_name=timezone_name)

        if self._is_trigger_time(local_now, hour=8, minute=0):
            text = self._build_morning_brief(user=user, local_now=local_now, task_lines=task_lines)
            if text:
                await self._send_once(
                    user=user,
                    dedupe_key=f"morning_brief:{local_now.date().isoformat()}",
                    event_type="morning_brief",
                    scheduled_at=local_now.replace(hour=8, minute=0, second=0, microsecond=0),
                    text=text,
                    now=now,
                )

        await self._send_prestart_reminders(user=user, local_now=local_now, tasks=tasks, timezone_name=timezone_name, now=now)

        if self._is_trigger_time(local_now, hour=23, minute=30):
            text = self._build_evening_review(local_now=local_now, task_lines=task_lines)
            if text:
                await self._send_once(
                    user=user,
                    dedupe_key=f"evening_review:{local_now.date().isoformat()}",
                    event_type="evening_review",
                    scheduled_at=local_now.replace(hour=23, minute=30, second=0, microsecond=0),
                    text=text,
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
            due_at = self._parse_ticktick_datetime(task.dueDate, timezone_name=timezone_name)
            if due_at is None:
                continue
            scheduled_at = due_at - timedelta(minutes=5)
            if not (scheduled_at <= local_now < due_at):
                continue
            description = task.desc or task.content or None
            text = f"还有 5 分钟：{self._renderer.render_weekday(due_at)} {due_at.strftime('%H:%M')} {task.title}"
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
            )

    def _build_morning_brief(
        self,
        *,
        user: User,
        local_now: datetime,
        task_lines: list[dict],
    ) -> str:
        today = local_now.date()
        scheduled_items = [item for item in task_lines if item["date"] == today.isoformat()]
        scheduled_items.sort(key=lambda item: item["sort_key"])
        ddl_items = [
            item
            for item in task_lines
            if today < datetime.fromisoformat(item["date"]).date() <= today + timedelta(days=7)
        ]
        ddl_items.sort(key=lambda item: item["sort_key"])

        with self._session_factory() as session:
            windowed_items = self._build_windowed_items(
                user_id=user.id,
                local_now=local_now,
                task_shadow_repo=TaskShadowRepository(session),
            )

        top_items = scheduled_items[:3] if scheduled_items else ddl_items[:3]
        return self._briefing_service.render_morning_brief(
            top_items=top_items,
            scheduled_items=scheduled_items[:8],
            ddl_items=ddl_items[:8],
            windowed_items=windowed_items,
        )

    def _build_evening_review(self, *, local_now: datetime, task_lines: list[dict]) -> str | None:
        today_items = [item for item in task_lines if item["date"] == local_now.date().isoformat()]
        today_items.sort(key=lambda item: item["sort_key"])
        if not today_items:
            return "今天快结束啦。我这边没看到明确落在今天的安排；如果有想顺手收尾的事，也可以直接跟我说。"

        lines = ["一天快收尾啦。今天这些事哪些已经做完了？"]
        for item in today_items[:8]:
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
                    payload_json={"text": text},
                    status="sent",
                    sent_at=now,
                )
            )
            session.commit()
            reminder_repo.mark_sent(event, sent_at=now)

    def _build_windowed_items(
        self,
        *,
        user_id: int,
        local_now: datetime,
        task_shadow_repo: TaskShadowRepository,
    ) -> list[dict]:
        items: list[dict] = []
        for shadow in task_shadow_repo.list_windowed_by_user(user_id=user_id):
            if shadow.window_end is None:
                continue
            if shadow.window_end.date() < local_now.date():
                continue
            if shadow.window_start is not None and shadow.window_start.date() > (local_now.date() + timedelta(days=7)):
                continue
            items.append(
                {
                    "date": (shadow.window_end.date().isoformat()),
                    "sort_key": shadow.window_end,
                    "weekday": self._renderer.render_weekday(shadow.window_end),
                    "when": shadow.raw_nl_time or "时间窗口",
                    "title": shadow.normalized_title or "待推进事项",
                    "description": None,
                }
            )
        items.sort(key=lambda item: item["sort_key"])
        return items[:8]

    def _build_task_lines(self, *, tasks: list[TickTickTask], timezone_name: str) -> list[dict]:
        lines: list[dict] = []
        for task in tasks:
            if task.status == 2 or task.completed:
                continue
            effective_dt = self._parse_ticktick_datetime(task.dueDate or task.startDate, timezone_name=timezone_name)
            if effective_dt is None:
                continue
            lines.append(
                {
                    "date": effective_dt.date().isoformat(),
                    "sort_key": effective_dt,
                    "weekday": self._renderer.render_weekday(effective_dt),
                    "when": "今天" if task.isAllDay else effective_dt.strftime("%H:%M"),
                    "title": task.title,
                    "description": task.desc or task.content or None,
                }
            )
        return lines

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
