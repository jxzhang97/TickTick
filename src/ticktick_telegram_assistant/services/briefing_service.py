from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


class BriefingService:
    def __init__(self, renderer: MessageRenderer | None = None) -> None:
        self._renderer = renderer or MessageRenderer()

    def render_morning_brief(
        self,
        *,
        top_items: list[dict],
        scheduled_items: list[dict],
        ddl_items: list[dict],
        windowed_items: list[dict],
    ) -> str:
        sections: list[str] = ["早呀，今天先抓重点："]
        if top_items:
            sections.append("最重要的几件：")
            sections.extend(f"- {self._renderer.render_task_line(item)}" for item in top_items)
        if scheduled_items:
            sections.append("今天有明确时间的安排：")
            sections.extend(f"- {self._renderer.render_task_line(item)}" for item in scheduled_items)
        if ddl_items:
            sections.append("未来 7 天的 ddl：")
            sections.extend(f"- {self._renderer.render_task_line(item)}" for item in ddl_items)
        if windowed_items:
            sections.append("这几天要推进的事：")
            sections.extend(f"- {self._renderer.render_task_line(item)}" for item in windowed_items)
        return "\n".join(sections)
