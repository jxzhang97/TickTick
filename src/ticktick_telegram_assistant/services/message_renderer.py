from datetime import datetime


class MessageRenderer:
    def render_weekday(self, dt: datetime) -> str:
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]

    def render_date_anchor(self, dt: datetime) -> str:
        return f"{dt.strftime('%Y-%m-%d')} {self.render_weekday(dt)}"

    def render_task_line(self, item: dict) -> str:
        note = item.get("note")
        if note:
            return str(note)
        date_label = item.get("date_label")
        prefix = date_label or item.get("weekday", "")
        line = f"{prefix} {item.get('when', '')} {item.get('title', '')}".strip()
        description = item.get("description")
        if description:
            line = f"{line}，{self._summarize_description(str(description))}"
        return line

    def render_brief_item(self, item: dict, *, section: str) -> str:
        note = item.get("note")
        if note:
            return str(note)

        if section == "today_timed":
            line = f"{item.get('title', '')} | {item.get('time_label', '')}".strip(" |")
            return self._append_description(line, item)

        if section == "today_date_only":
            line = f"{item.get('title', '')} | 今天处理".strip(" |")
            return self._append_description(line, item)

        if section == "overdue":
            deadline = self._format_due(item)
            line = f"{item.get('title', '')} | 已逾期，原截止 {deadline}".strip()
            return self._append_description(line, item)

        if section == "upcoming_explicit":
            deadline = self._format_due(item)
            line = f"{item.get('title', '')} | 截止 {deadline}".strip()
            return self._append_description(line, item)

        if section == "windowed_active":
            end_label = self._format_short_date(item.get("window_end"))
            raw_time = item.get("raw_nl_time") or "这段时间"
            line = f"{item.get('title', '')} | {raw_time}，先在 {end_label} 前推进".strip()
            return self._append_description(line, item)

        if section == "windowed_upcoming":
            start_label = self._format_short_date(item.get("window_start"))
            end_label = self._format_short_date(item.get("window_end"))
            raw_time = item.get("raw_nl_time") or "时间窗口"
            line = f"{item.get('title', '')} | {raw_time}（{start_label} 到 {end_label}）".strip()
            return self._append_description(line, item)

        if section == "memo":
            line = str(item.get("title", "")).strip()
            return self._append_description(line, item)

        return self.render_task_line(item)

    def _summarize_description(self, description: str, *, limit: int = 28) -> str:
        normalized = " ".join(description.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 1]}…"

    def _append_description(self, line: str, item: dict) -> str:
        description = item.get("description")
        if not description:
            return line
        return f"{line}（说明：{self._summarize_description(str(description), limit=40)}）"

    def _format_due(self, item: dict) -> str:
        due_dt = item.get("due_dt") or item.get("sort_key")
        if not isinstance(due_dt, datetime):
            return ""
        return self._format_datetime(due_dt, include_time=not bool(item.get("is_all_day")))

    def _format_short_date(self, raw: datetime | None) -> str:
        if raw is None:
            return "这周"
        return f"{raw.strftime('%m-%d')} {self.render_weekday(raw)}"

    def _format_datetime(self, dt: datetime, *, include_time: bool) -> str:
        base = self._format_short_date(dt)
        if include_time:
            return f"{base} {dt.strftime('%H:%M')}"
        return base
