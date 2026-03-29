from datetime import datetime


class MessageRenderer:
    def render_weekday(self, dt: datetime) -> str:
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]

    def render_task_line(self, item: dict) -> str:
        note = item.get("note")
        if note:
            return str(note)
        line = f"{item.get('weekday', '')} {item.get('when', '')} {item.get('title', '')}".strip()
        description = item.get("description")
        if description:
            line = f"{line}，{self._summarize_description(str(description))}"
        return line

    def _summarize_description(self, description: str, *, limit: int = 28) -> str:
        normalized = " ".join(description.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 1]}…"
