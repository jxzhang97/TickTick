from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient, TickTickTask
from ticktick_telegram_assistant.repositories.task_shadows import TaskShadowRepository
from ticktick_telegram_assistant.services.briefing_service import BriefingService
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


class TodayBriefService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        ticktick_client: TickTickClient,
        briefing_service: BriefingService | None = None,
        renderer: MessageRenderer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client
        self._briefing_service = briefing_service or BriefingService(renderer=renderer)
        self._renderer = renderer or MessageRenderer()

    async def build_today_brief(self, *, telegram_user_id: str, now: datetime | None = None) -> str:
        user = self._get_user(telegram_user_id=telegram_user_id)
        if user is None or not user.ticktick_access_token:
            return "我现在还没拿到你的 TickTick 访问权限，所以还不能替你拉今天的安排。"

        timezone_name = user.current_timezone or "America/Los_Angeles"
        current_time = now or datetime.now(ZoneInfo(timezone_name))
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=ZoneInfo(timezone_name))
        else:
            current_time = current_time.astimezone(ZoneInfo(timezone_name))

        tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token)
        task_lines = [item for item in (self._task_to_line(task, timezone_name=timezone_name) for task in tasks) if item is not None]
        today = current_time.date()
        scheduled_items = [item for item in task_lines if item["date"] == today.isoformat()]
        scheduled_items.sort(key=lambda item: item["sort_key"])
        ddl_items = [
            item
            for item in task_lines
            if today < item["date_obj"] <= today + timedelta(days=7)
        ]
        ddl_items.sort(key=lambda item: item["sort_key"])

        windowed_items = self._build_windowed_items(user=user, current_time=current_time, timezone_name=timezone_name)
        top_items = scheduled_items[:3] if scheduled_items else ddl_items[:3]

        if not (top_items or scheduled_items or ddl_items or windowed_items):
            return "今天在 TickTick 里我还没看到明确落在今天的安排，你可以放心一点。"

        return self._briefing_service.render_morning_brief(
            top_items=top_items,
            scheduled_items=scheduled_items[:8],
            ddl_items=ddl_items[:8],
            windowed_items=windowed_items,
        )

    def _get_user(self, *, telegram_user_id: str) -> User | None:
        with self._session_factory() as session:
            return session.query(User).filter(User.telegram_user_id == telegram_user_id).one_or_none()

    def _task_to_line(self, task: TickTickTask, *, timezone_name: str) -> dict | None:
        if task.status == 2 or task.completed:
            return None

        start_dt = self._parse_ticktick_datetime(task.startDate, timezone_name=timezone_name)
        due_dt = self._parse_ticktick_datetime(task.dueDate, timezone_name=timezone_name)
        effective_dt = start_dt or due_dt
        if effective_dt is None:
            return None

        description = task.desc or task.content or None
        if task.isAllDay:
            when = "今天"
        elif start_dt is not None and due_dt is not None and due_dt > start_dt:
            when = f"{start_dt.strftime('%H:%M')}-{due_dt.strftime('%H:%M')}"
        else:
            when = effective_dt.strftime("%H:%M")
        return {
            "date": effective_dt.date().isoformat(),
            "date_obj": effective_dt.date(),
            "sort_key": effective_dt,
            "weekday": self._renderer.render_weekday(effective_dt),
            "when": when,
            "title": task.title,
            "description": description,
        }

    def _build_windowed_items(self, *, user: User, current_time: datetime, timezone_name: str) -> list[dict]:
        items: list[dict] = []
        with self._session_factory() as session:
            for shadow in TaskShadowRepository(session).list_windowed_by_user(user_id=user.id):
                if shadow.window_end is None:
                    continue
                if shadow.window_end.date() < current_time.date():
                    continue
                if shadow.window_start is not None and shadow.window_start.date() > (current_time.date() + timedelta(days=7)):
                    continue
                items.append(
                    {
                        "date": shadow.window_end.date().isoformat(),
                        "date_obj": shadow.window_end.date(),
                        "sort_key": shadow.window_end,
                        "weekday": self._renderer.render_weekday(shadow.window_end),
                        "when": shadow.raw_nl_time or "时间窗口",
                        "title": shadow.normalized_title or "待推进事项",
                        "description": None,
                    }
                )
        items.sort(key=lambda item: item["sort_key"])
        return items[:8]

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
