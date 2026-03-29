from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient, TickTickTask
from ticktick_telegram_assistant.repositories.task_shadows import TaskShadowRepository
from ticktick_telegram_assistant.services.briefing_service import BriefingService
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


@dataclass
class TaskBriefSnapshot:
    current_time: datetime
    timezone_name: str
    today_timed_items: list[dict]
    today_date_only_items: list[dict]
    active_windowed_items: list[dict]
    overdue_items: list[dict]
    upcoming_explicit_items: list[dict]
    upcoming_windowed_items: list[dict]
    memo_items: list[dict]


class TodayBriefService:
    _TOP_ITEMS_LIMIT = 3

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
        return await self.build_today_brief_for_user(user=user, now=now)

    async def build_snapshot(
        self,
        *,
        telegram_user_id: str,
        now: datetime | None = None,
        tasks: list[TickTickTask] | None = None,
    ) -> TaskBriefSnapshot | None:
        user = self._get_user(telegram_user_id=telegram_user_id)
        return await self.build_snapshot_for_user(user=user, now=now, tasks=tasks)

    async def build_today_brief_for_user(
        self,
        *,
        user: User | None,
        now: datetime | None = None,
        tasks: list[TickTickTask] | None = None,
    ) -> str:
        snapshot = await self.build_snapshot_for_user(user=user, now=now, tasks=tasks)
        if snapshot is None:
            return "我现在还没拿到你的 TickTick 访问权限，所以还不能替你拉今天的安排。"

        return self._briefing_service.render_morning_brief(
            current_time=snapshot.current_time,
            today_timed_items=snapshot.today_timed_items,
            today_date_only_items=snapshot.today_date_only_items,
            active_windowed_items=snapshot.active_windowed_items,
            overdue_items=snapshot.overdue_items,
            upcoming_explicit_items=snapshot.upcoming_explicit_items,
            upcoming_windowed_items=snapshot.upcoming_windowed_items,
            memo_items=snapshot.memo_items,
        )

    async def build_snapshot_for_user(
        self,
        *,
        user: User | None,
        now: datetime | None = None,
        tasks: list[TickTickTask] | None = None,
    ) -> TaskBriefSnapshot | None:
        if user is None or not user.ticktick_access_token:
            return None

        timezone_name = user.current_timezone or "America/Los_Angeles"
        current_time = now or datetime.now(ZoneInfo(timezone_name))
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=ZoneInfo(timezone_name))
        else:
            current_time = current_time.astimezone(ZoneInfo(timezone_name))

        fetched_tasks = tasks
        if fetched_tasks is None:
            fetched_tasks = await self._ticktick_client.list_tasks(access_token=user.ticktick_access_token)
        today = current_time.date()
        windowed_shadows, memo_shadows = self._load_shadow_groups(user_id=user.id)
        windowed_task_ids = {shadow.ticktick_task_id for shadow in windowed_shadows}
        memo_task_ids = {shadow.ticktick_task_id for shadow in memo_shadows}
        task_records = [
            item
            for item in (
                self._task_to_record(
                    task,
                    timezone_name=timezone_name,
                    windowed_task_ids=windowed_task_ids,
                    memo_task_ids=memo_task_ids,
                )
                for task in fetched_tasks
            )
            if item is not None
        ]
        tasks_by_id = {task.id: task for task in fetched_tasks if task.status != 2 and not task.completed}

        today_timed_items = self._annotate_section(
            sorted(
                [
                    item
                    for item in task_records
                    if item["semantic_type"] != "windowed"
                    and item["date_obj"] == today
                    and not item["is_all_day"]
                ],
                key=lambda item: item["sort_key"],
            ),
            "today_timed",
        )
        today_date_only_items = self._annotate_section(
            sorted(
                [
                    item
                    for item in task_records
                    if item["semantic_type"] != "windowed"
                    and item["date_obj"] == today
                    and item["is_all_day"]
                ],
                key=lambda item: item["sort_key"],
            ),
            "today_date_only",
        )
        overdue_items = self._annotate_section(
            sorted(
                [
                    item
                    for item in task_records
                    if item["semantic_type"] != "windowed"
                    and item["date_obj"] is not None
                    and item["date_obj"] < today
                ],
                key=lambda item: item["sort_key"],
            ),
            "overdue",
        )
        upcoming_explicit_items = self._annotate_section(
            sorted(
                [
                    item
                    for item in task_records
                    if item["semantic_type"] != "windowed"
                    and item["date_obj"] is not None
                    and today < item["date_obj"] <= today + timedelta(days=7)
                ],
                key=lambda item: item["sort_key"],
            ),
            "upcoming_explicit",
        )
        active_windowed_items, upcoming_windowed_items = self._build_windowed_items(
            windowed_shadows=windowed_shadows,
            tasks_by_id=tasks_by_id,
            current_time=current_time,
        )
        memo_items = self._build_memo_items(
            task_records=task_records,
            memo_shadows=memo_shadows,
            tasks_by_id=tasks_by_id,
        )

        return TaskBriefSnapshot(
            current_time=current_time,
            timezone_name=timezone_name,
            today_timed_items=today_timed_items,
            today_date_only_items=today_date_only_items,
            active_windowed_items=active_windowed_items,
            overdue_items=overdue_items,
            upcoming_explicit_items=upcoming_explicit_items,
            upcoming_windowed_items=upcoming_windowed_items,
            memo_items=memo_items,
        )

    def _annotate_section(self, items: list[dict], section: str) -> list[dict]:
        return [dict(item, section=section) for item in items]

    def _get_user(self, *, telegram_user_id: str) -> User | None:
        with self._session_factory() as session:
            return session.query(User).filter(User.telegram_user_id == telegram_user_id).one_or_none()

    def _task_to_record(
        self,
        task: TickTickTask,
        *,
        timezone_name: str,
        windowed_task_ids: set[str],
        memo_task_ids: set[str],
    ) -> dict | None:
        if task.status == 2 or task.completed:
            return None

        start_dt = self._parse_ticktick_datetime(task.startDate, timezone_name=timezone_name)
        due_dt = self._parse_ticktick_datetime(task.dueDate, timezone_name=timezone_name)
        effective_dt = start_dt or due_dt
        description = task.desc or task.content or None
        semantic_type = self._infer_semantic_type(
            task=task,
            effective_dt=effective_dt,
            windowed_task_ids=windowed_task_ids,
            memo_task_ids=memo_task_ids,
        )
        if task.isAllDay:
            time_label = "全天"
        elif start_dt is not None and due_dt is not None and due_dt > start_dt:
            time_label = f"{start_dt.strftime('%H:%M')}-{due_dt.strftime('%H:%M')}"
        elif effective_dt is not None:
            time_label = effective_dt.strftime("%H:%M")
        else:
            time_label = ""
        return {
            "task_id": task.id,
            "date": effective_dt.date().isoformat() if effective_dt is not None else None,
            "date_obj": effective_dt.date() if effective_dt is not None else None,
            "sort_key": effective_dt or datetime.max.replace(tzinfo=ZoneInfo(timezone_name)),
            "weekday": self._renderer.render_weekday(effective_dt) if effective_dt is not None else None,
            "when": time_label,
            "time_label": time_label,
            "title": task.title,
            "description": description,
            "priority": task.priority or 0,
            "is_all_day": bool(task.isAllDay),
            "is_time_span": bool(start_dt is not None and due_dt is not None and due_dt > start_dt),
            "semantic_type": semantic_type,
            "start_dt": start_dt,
            "due_dt": due_dt,
            "effective_dt": effective_dt,
        }

    def _load_shadow_groups(self, *, user_id: int) -> tuple[list[TaskShadow], list[TaskShadow]]:
        with self._session_factory() as session:
            repository = TaskShadowRepository(session)
            return repository.list_windowed_by_user(user_id=user_id), repository.list_memo_by_user(user_id=user_id)

    def _build_windowed_items(
        self,
        *,
        windowed_shadows: list[TaskShadow],
        tasks_by_id: dict[str, TickTickTask],
        current_time: datetime,
    ) -> tuple[list[dict], list[dict]]:
        active_items: list[dict] = []
        upcoming_items: list[dict] = []
        today = current_time.date()
        horizon = today + timedelta(days=7)
        for shadow in windowed_shadows:
            if shadow.window_end is None:
                continue
            if shadow.window_end.date() < today:
                continue
            start_date = shadow.window_start.date() if shadow.window_start is not None else today
            task = tasks_by_id.get(shadow.ticktick_task_id)
            item = {
                "task_id": shadow.ticktick_task_id,
                "title": task.title if task is not None else (shadow.normalized_title or "待推进事项"),
                "description": (task.desc or task.content or None) if task is not None else None,
                "window_start": shadow.window_start,
                "window_end": shadow.window_end,
                "raw_nl_time": shadow.raw_nl_time,
                "sort_key": shadow.window_start or shadow.window_end,
            }
            if start_date <= today <= shadow.window_end.date():
                active_items.append(item)
            elif today < start_date <= horizon:
                upcoming_items.append(item)
        active_items.sort(key=lambda item: item["sort_key"])
        upcoming_items.sort(key=lambda item: item["sort_key"])
        return self._annotate_section(active_items, "windowed_active"), self._annotate_section(
            upcoming_items,
            "windowed_upcoming",
        )

    def _build_memo_items(
        self,
        *,
        task_records: list[dict],
        memo_shadows: list[TaskShadow],
        tasks_by_id: dict[str, TickTickTask],
    ) -> list[dict]:
        items: list[dict] = []
        seen_task_ids: set[str] = set()
        for item in task_records:
            if item["semantic_type"] != "memo":
                continue
            items.append(item)
            seen_task_ids.add(item["task_id"])
        for shadow in memo_shadows:
            if shadow.ticktick_task_id in seen_task_ids:
                continue
            task = tasks_by_id.get(shadow.ticktick_task_id)
            items.append(
                {
                    "task_id": shadow.ticktick_task_id,
                    "title": task.title if task is not None else (shadow.normalized_title or "待整理备忘"),
                    "description": (task.desc or task.content or None) if task is not None else None,
                    "semantic_type": "memo",
                    "sort_key": datetime.max.replace(tzinfo=ZoneInfo("UTC")),
                }
            )
        items.sort(key=lambda item: (-(item.get("priority") or 0), item.get("title") or ""))
        return self._annotate_section(items, "memo")

    def _infer_semantic_type(
        self,
        *,
        task: TickTickTask,
        effective_dt: datetime | None,
        windowed_task_ids: set[str],
        memo_task_ids: set[str],
    ) -> str:
        if task.id in windowed_task_ids:
            return "windowed"
        if task.id in memo_task_ids:
            return "memo"
        if effective_dt is None:
            return "memo"
        return "explicit_time"

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
