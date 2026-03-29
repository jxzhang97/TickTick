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
        for item in scheduled_items:
            item["category"] = "scheduled"
        windowed_items = self._build_windowed_items(user=user, current_time=current_time, timezone_name=timezone_name)
        ddl_items = self._build_deadline_items(task_lines=task_lines, today=today)
        top_items = self._select_top_items(
            scheduled_items=scheduled_items,
            ddl_items=ddl_items,
            windowed_items=windowed_items,
        )
        top_ids = {item.get("task_id") for item in top_items if item.get("task_id")}
        scheduled_detail_items = self._build_detail_section(
            items=scheduled_items,
            excluded_ids=top_ids,
            fallback_note="重点都在上面了。",
        )
        ddl_detail_items = ddl_items
        windowed_detail_items = self._build_detail_section(
            items=windowed_items,
            excluded_ids=top_ids,
            fallback_note="重点都在上面了。",
        )

        if not (top_items or scheduled_items or ddl_items or windowed_items):
            return "今天在 TickTick 里我还没看到明确落在今天的安排，你可以放心一点。"

        return self._briefing_service.render_morning_brief(
            top_items=top_items,
            scheduled_items=scheduled_detail_items,
            ddl_items=ddl_detail_items,
            windowed_items=windowed_detail_items,
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
            "task_id": task.id,
            "date": effective_dt.date().isoformat(),
            "date_obj": effective_dt.date(),
            "sort_key": effective_dt,
            "weekday": self._renderer.render_weekday(effective_dt),
            "when": when,
            "title": task.title,
            "description": description,
            "priority": task.priority or 0,
            "is_time_span": bool(start_dt is not None and due_dt is not None and due_dt > start_dt),
            "category": "future",
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
                        "task_id": shadow.ticktick_task_id,
                        "date": shadow.window_end.date().isoformat(),
                        "date_obj": shadow.window_end.date(),
                        "sort_key": shadow.window_end,
                        "weekday": self._renderer.render_weekday(shadow.window_end),
                        "when": shadow.raw_nl_time or "时间窗口",
                        "title": shadow.normalized_title or "待推进事项",
                        "description": None,
                        "priority": 0,
                        "is_time_span": False,
                        "category": "windowed",
                    }
                )
        items.sort(key=lambda item: item["sort_key"])
        return items[:8]

    def _build_deadline_items(self, *, task_lines: list[dict], today) -> list[dict]:
        items = [
            item
            for item in task_lines
            if today < item["date_obj"] <= today + timedelta(days=7) and not item.get("is_time_span")
        ]
        items.sort(key=lambda item: item["sort_key"])
        for item in items:
            item["category"] = "ddl"
        return items

    def _select_top_items(
        self,
        *,
        scheduled_items: list[dict],
        ddl_items: list[dict],
        windowed_items: list[dict],
    ) -> list[dict]:
        candidates = [
            *scheduled_items,
            *ddl_items,
            *windowed_items,
        ]
        ranked = sorted(candidates, key=self._top_sort_key)
        return ranked[:5]

    def _top_sort_key(self, item: dict) -> tuple[int, int, datetime]:
        category_rank = {
            "scheduled": 0,
            "ddl": 1,
            "windowed": 2,
        }.get(item.get("category"), 3)
        priority = -(item.get("priority") or 0)
        return (category_rank, priority, item["sort_key"])

    def _build_detail_section(self, *, items: list[dict], excluded_ids: set[str], fallback_note: str) -> list[dict]:
        detail_items = [item for item in items if item.get("task_id") not in excluded_ids]
        if detail_items:
            return detail_items
        if items:
            return [{"note": fallback_note}]
        return []

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
