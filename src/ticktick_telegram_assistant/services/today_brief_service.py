from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient, TickTickTask
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


class TodayBriefService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        ticktick_client: TickTickClient,
        renderer: MessageRenderer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client
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
        todays_tasks = [
            item for item in (self._task_to_line(task, timezone_name=timezone_name) for task in tasks)
            if item is not None and item["date"] == current_time.date().isoformat()
        ]

        if not todays_tasks:
            return "今天在 TickTick 里我还没看到明确落在今天的安排，你可以放心一点。"

        todays_tasks.sort(key=lambda item: item["sort_key"])
        lines = ["今天我先帮你抓重点："]
        for item in todays_tasks[:5]:
            lines.append(f"- {self._renderer.render_task_line(item)}")
        return "\n".join(lines)

    def _get_user(self, *, telegram_user_id: str) -> User | None:
        with self._session_factory() as session:
            return session.query(User).filter(User.telegram_user_id == telegram_user_id).one_or_none()

    def _task_to_line(self, task: TickTickTask, *, timezone_name: str) -> dict | None:
        if task.status == 2 or task.completed:
            return None

        effective_dt = self._parse_ticktick_datetime(task.dueDate or task.startDate, timezone_name=timezone_name)
        if effective_dt is None:
            return None

        description = task.desc or task.content or None
        when = "今天" if task.isAllDay else effective_dt.strftime("%H:%M")
        return {
            "date": effective_dt.date().isoformat(),
            "sort_key": effective_dt,
            "weekday": self._renderer.render_weekday(effective_dt),
            "when": when,
            "title": task.title,
            "description": description,
        }

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
