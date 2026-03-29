from datetime import datetime

from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


class BriefingService:
    _SECTION_LIMIT = 5

    def __init__(self, renderer: MessageRenderer | None = None) -> None:
        self._renderer = renderer or MessageRenderer()

    def render_morning_brief(
        self,
        *,
        current_time: datetime,
        today_timed_items: list[dict],
        today_date_only_items: list[dict],
        active_windowed_items: list[dict],
        overdue_items: list[dict],
        upcoming_explicit_items: list[dict],
        upcoming_windowed_items: list[dict],
        memo_items: list[dict],
    ) -> str:
        sections: list[str] = [
            f"早呀，{self._renderer.render_date_anchor(current_time)} 的安排我帮你整理好了，我们一起看一眼。",
            "",
            "今天有明确时间的任务",
        ]
        sections.extend(
            self._render_section_items(
                today_timed_items,
                section="today_timed",
                empty_text="今天没有明确到钟点的任务。",
            )
        )
        sections.append("")
        sections.append("今天要留意的日期任务")
        sections.extend(
            self._render_section_items(
                today_date_only_items,
                section="today_date_only",
                empty_text="今天没有只写日期但没写具体时间的任务。",
            )
        )
        sections.append("")
        sections.append("这段时间可以找空推进的事")
        sections.extend(
            self._render_section_items(
                active_windowed_items,
                section="windowed_active",
                empty_text="目前没有正在进行中的弹性时间段任务，你可以更从容地把空档填进想做的事。",
            )
        )
        sections.append("")
        sections.append("未完成的事情提醒（需要跟进的截止项）")
        sections.extend(
            self._render_section_items(
                overdue_items,
                section="overdue",
                empty_text="目前没有需要补追的截止项。",
            )
        )
        sections.append("")
        sections.append("接下来 7 天的明确安排和截止提醒")
        sections.extend(
            self._render_section_items(
                upcoming_explicit_items,
                section="upcoming_explicit",
                empty_text="未来 7 天还没有新的明确安排或截止提醒。",
            )
        )
        sections.append("")
        sections.append("未来 7 天里适合找空完成的事")
        sections.extend(
            self._render_section_items(
                upcoming_windowed_items,
                section="windowed_upcoming",
                empty_text="未来 7 天内还没有新可安排的弹性时间段任务，可按需把其他事项排进空档。",
            )
        )
        sections.append("")
        sections.append("顺手记着的小备忘（暂未安排具体时间，记下以便后续安排）")
        sections.extend(
            self._render_section_items(
                memo_items,
                section="memo",
                empty_text="暂时没有还没安排时间的小备忘。",
            )
        )
        return "\n".join(sections)

    def _render_section_items(self, items: list[dict], *, section: str, empty_text: str) -> list[str]:
        if not items:
            return [f"- {empty_text}"]
        rendered = [f"- {self._renderer.render_brief_item(item, section=section)}" for item in items[: self._SECTION_LIMIT]]
        remaining = len(items) - self._SECTION_LIMIT
        if remaining > 0:
            rendered.append(f"- 其余 {remaining} 件我先略写，真忙的话先抓上面这些。")
        return rendered
