from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


class BriefingService:
    _SECTION_LIMIT = 5

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
        sections: list[str] = ["早呀，今天先看这几块："]
        sections.append("今天最重要的几件：")
        sections.extend(self._render_section_items(top_items, empty_text="今天暂时还没有特别冒尖的重点。"))
        sections.append("今天有明确时间的安排：")
        sections.extend(self._render_section_items(scheduled_items, empty_text="暂时没有。"))
        sections.append("未来 7 天的 ddl：")
        sections.extend(self._render_section_items(ddl_items, empty_text="暂时没有。"))
        sections.append("这几天要推进的时间窗口任务：")
        sections.extend(self._render_section_items(windowed_items, empty_text="暂时没有。"))
        return "\n".join(sections)

    def _render_section_items(self, items: list[dict], *, empty_text: str) -> list[str]:
        if not items:
            return [f"- {empty_text}"]
        rendered = [f"- {self._renderer.render_task_line(item)}" for item in items[: self._SECTION_LIMIT]]
        remaining = len(items) - self._SECTION_LIMIT
        if remaining > 0:
            rendered.append(f"- 其余 {remaining} 件我先略写，真忙的话先抓上面这些。")
        return rendered
