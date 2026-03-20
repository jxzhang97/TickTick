from datetime import datetime


class MessageRenderer:
    def render_weekday(self, dt: datetime) -> str:
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]

    def render_task_line(self, item: dict) -> str:
        line = f"{item.get('weekday', '')} {item.get('when', '')} {item.get('title', '')}".strip()
        description = item.get("description")
        if description:
            line = f"{line}，{description}"
        return line
