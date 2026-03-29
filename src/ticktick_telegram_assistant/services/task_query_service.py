from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.user import User
from ticktick_telegram_assistant.domain.schemas import PlannedQueryIntent
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer
from ticktick_telegram_assistant.services.today_brief_service import TaskBriefSnapshot, TodayBriefService


class TaskQueryService:
    _SECTION_LIMIT = 6

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        ticktick_client,
        today_brief_service: TodayBriefService,
        renderer: MessageRenderer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client
        self._today_brief_service = today_brief_service
        self._renderer = renderer or MessageRenderer()

    async def build_query_reply(
        self,
        *,
        telegram_user_id: str,
        query: PlannedQueryIntent,
        now: datetime | None = None,
    ) -> str:
        scope = self._resolve_scope(query)
        if scope == "today":
            return await self._today_brief_service.build_today_brief(
                telegram_user_id=telegram_user_id,
                now=now,
            )

        snapshot = await self._today_brief_service.build_snapshot(
            telegram_user_id=telegram_user_id,
            now=now,
        )
        if snapshot is None:
            return "我现在还没拿到你的 TickTick 访问权限，所以还不能替你拉这些安排。"

        if scope == "tomorrow":
            reply = self._render_custom_range(
                snapshot=snapshot,
                query=query,
                header="我把明天要留意的事整理好了。",
                range_start=self._start_of_day(snapshot.current_time + timedelta(days=1)),
                range_end=self._end_of_day(snapshot.current_time + timedelta(days=1)),
            )
            return self._prepend_stale_note(snapshot=snapshot, reply=reply)
        if scope == "overdue":
            return self._prepend_stale_note(snapshot=snapshot, reply=self._render_overdue(snapshot))
        if scope == "this_week":
            return self._prepend_stale_note(snapshot=snapshot, reply=self._render_this_week(snapshot))
        if scope == "custom_range":
            range_start = query.range_start or self._start_of_day(snapshot.current_time)
            range_end = query.range_end or self._end_of_day(snapshot.current_time + timedelta(days=7))
            reply = self._render_custom_range(
                snapshot=snapshot,
                query=query,
                header=f"我按 {self._renderer.render_date_anchor(range_start)} 到 {self._renderer.render_date_anchor(range_end)} 帮你捋了一遍。",
                range_start=range_start,
                range_end=range_end,
            )
            return self._prepend_stale_note(snapshot=snapshot, reply=reply)
        if scope == "upcoming":
            return self._prepend_stale_note(snapshot=snapshot, reply=self._render_upcoming(snapshot))
        return self._prepend_stale_note(snapshot=snapshot, reply=self._render_recent(snapshot))

    def _prepend_stale_note(self, *, snapshot: TaskBriefSnapshot, reply: str) -> str:
        if not snapshot.is_stale:
            return reply
        stale_anchor = (
            self._renderer.render_date_anchor(snapshot.snapshot_synced_at)
            if snapshot.snapshot_synced_at is not None
            else "刚才"
        )
        return f"注：刚刚没拉到 TickTick 实时数据，我先用 {stale_anchor} 的本地快照帮你顶上，可能会有一点点旧。\n\n{reply}"

    def _render_recent(self, snapshot: TaskBriefSnapshot) -> str:
        explicit_items = self._sorted_explicit_items(
            snapshot.overdue_items
            + snapshot.today_timed_items
            + snapshot.today_date_only_items
            + snapshot.upcoming_explicit_items
        )
        sections = [
            "我把最近这段时间要留意的事捋了一遍。",
            "",
            "最近还挂着的截止项",
            *self._render_items(
                snapshot.overdue_items,
                empty_text="目前没有还挂着的逾期截止项。",
            ),
            "",
            "这几天明确安排和截止提醒",
            *self._render_items(
                explicit_items,
                empty_text="最近几天暂时没有新的明确安排或截止提醒。",
            ),
            "",
            "这段时间可以找空推进的事",
            *self._render_items(
                snapshot.active_windowed_items + snapshot.upcoming_windowed_items,
                empty_text="目前没有正在推进中的弹性时间段任务。",
            ),
        ]
        return "\n".join(sections)

    def _render_upcoming(self, snapshot: TaskBriefSnapshot) -> str:
        sections = [
            "我把接下来几天的事按节奏给你拎出来了。",
            "",
            "接下来 7 天的明确安排和截止提醒",
            *self._render_items(
                self._sorted_explicit_items(snapshot.upcoming_explicit_items),
                empty_text="未来 7 天还没有新的明确安排或截止提醒。",
            ),
            "",
            "接下来 7 天适合找空推进的事",
            *self._render_items(
                snapshot.upcoming_windowed_items,
                empty_text="未来 7 天内还没有新可安排的弹性时间段任务。",
            ),
        ]
        return "\n".join(sections)

    def _render_overdue(self, snapshot: TaskBriefSnapshot) -> str:
        sections = [
            "我先把还挂着的截止项列出来。",
            "",
            "未完成的事情提醒（需要跟进的截止项）",
            *self._render_items(
                snapshot.overdue_items,
                empty_text="目前没有还挂着的截止项。",
            ),
        ]
        return "\n".join(sections)

    def _render_this_week(self, snapshot: TaskBriefSnapshot) -> str:
        week_end = self._end_of_day(snapshot.current_time + timedelta(days=(6 - snapshot.current_time.weekday())))
        explicit_items = self._filter_explicit_range(
            snapshot=snapshot,
            range_start=self._start_of_day(snapshot.current_time),
            range_end=week_end,
        )
        windowed_items = self._filter_windowed_range(
            snapshot.active_windowed_items + snapshot.upcoming_windowed_items,
            range_start=self._start_of_day(snapshot.current_time),
            range_end=week_end,
        )
        sections = [
            "这周我先帮你按节奏整理好了。",
            "",
            "本周明确安排和截止提醒",
            *self._render_items(
                explicit_items,
                empty_text="本周还没有新的明确安排或截止提醒。",
            ),
            "",
            "本周适合找空推进的事",
            *self._render_items(
                windowed_items,
                empty_text="本周暂时没有新的弹性推进项。",
            ),
        ]
        return "\n".join(sections)

    def _render_custom_range(
        self,
        *,
        snapshot: TaskBriefSnapshot,
        query: PlannedQueryIntent,
        header: str,
        range_start: datetime,
        range_end: datetime,
    ) -> str:
        explicit_items = self._filter_explicit_range(
            snapshot=snapshot,
            range_start=range_start,
            range_end=range_end,
        )
        windowed_items = self._filter_windowed_range(
            snapshot.active_windowed_items + snapshot.upcoming_windowed_items,
            range_start=range_start,
            range_end=range_end,
        )
        sections = [
            header,
            "",
            "这段时间的明确安排和截止提醒",
            *self._render_items(
                explicit_items,
                empty_text="这段时间暂时没有明确安排或截止提醒。",
            ),
            "",
            "这段时间适合找空推进的事",
            *self._render_items(
                windowed_items,
                empty_text="这段时间没有新的弹性推进项。",
            ),
        ]
        return "\n".join(sections)

    def _resolve_scope(self, query: PlannedQueryIntent) -> str:
        raw_scope = (query.time_scope or "").strip().lower()
        if raw_scope in {"today", "tomorrow", "recent", "upcoming", "overdue", "this_week", "custom_range"}:
            return raw_scope

        query_text = (query.query_text or "").casefold()
        if query.query_kind == "today_brief" or any(token in query_text for token in ("今天", "今日")):
            return "today"
        if any(token in query_text for token in ("明天", "明日")):
            return "tomorrow"
        if any(token in query_text for token in ("最近", "这几天", "挂着什么", "还挂着")):
            return "recent"
        if any(token in query_text for token in ("未来", "接下来")):
            return "upcoming"
        if any(token in query_text for token in ("逾期", "没做完", "没完成", "截止项")):
            return "overdue"
        if any(token in query_text for token in ("这周", "本周")):
            return "this_week"
        return "recent"

    def _filter_explicit_range(
        self,
        *,
        snapshot: TaskBriefSnapshot,
        range_start: datetime,
        range_end: datetime,
    ) -> list[dict]:
        all_items = self._sorted_explicit_items(
            snapshot.overdue_items
            + snapshot.today_timed_items
            + snapshot.today_date_only_items
            + snapshot.upcoming_explicit_items
        )
        return [
            item
            for item in all_items
            if item.get("date_obj") is not None
            and range_start.date() <= item["date_obj"] <= range_end.date()
        ]

    def _filter_windowed_range(
        self,
        items: list[dict],
        *,
        range_start: datetime,
        range_end: datetime,
    ) -> list[dict]:
        filtered: list[dict] = []
        for item in items:
            window_start = item.get("window_start") or range_start
            window_end = item.get("window_end") or range_end
            if window_end < range_start or window_start > range_end:
                continue
            filtered.append(item)
        return sorted(filtered, key=lambda item: item.get("sort_key") or range_end)

    def _sorted_explicit_items(self, items: list[dict]) -> list[dict]:
        return sorted(
            items,
            key=lambda item: item.get("sort_key") or datetime.max.replace(tzinfo=item["sort_key"].tzinfo),
        )

    def _render_items(self, items: list[dict], *, empty_text: str) -> list[str]:
        if not items:
            return [f"- {empty_text}"]
        lines = [
            f"- {self._renderer.render_brief_item(item, section=item.get('section') or 'memo')}"
            for item in items[: self._SECTION_LIMIT]
        ]
        remaining = len(items) - self._SECTION_LIMIT
        if remaining > 0:
            lines.append(f"- 其余 {remaining} 件我先收着，真忙的话先抓上面这些。")
        return lines

    def _start_of_day(self, dt: datetime) -> datetime:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def _end_of_day(self, dt: datetime) -> datetime:
        return dt.replace(hour=23, minute=59, second=59, microsecond=0)
