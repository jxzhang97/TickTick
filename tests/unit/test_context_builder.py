from __future__ import annotations

from datetime import datetime

from ticktick_telegram_assistant.services.context_builder import ContextBuilder


def test_build_includes_memory_items_and_candidate_tasks() -> None:
    builder = ContextBuilder()

    context = builder.build(
        "老王，明天下午提醒我一下",
        current_timezone="America/Los_Angeles",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
        memory_items=[
            "alias_mapping: 老王 -> 王老师",
            "time_expression: 明天下午 -> 2026-03-28T15:00:00-07:00",
        ],
        recent_conversation_summaries=["用户刚说过希望回复简短。"],
        candidate_tasks=["task-1: 给导师发邮件"],
    )

    assert context.user_text == "老王，明天下午提醒我一下"
    assert context.current_timezone == "America/Los_Angeles"
    assert context.current_local_time == "2026-03-27T09:00:00-07:00"
    assert context.memory_items == [
        "alias_mapping: 老王 -> 王老师",
        "time_expression: 明天下午 -> 2026-03-28T15:00:00-07:00",
    ]
    assert context.recent_conversation_summaries == ["用户刚说过希望回复简短。"]
    assert context.candidate_tasks == ["task-1: 给导师发邮件"]


def test_split_lines_keeps_existing_behavior() -> None:
    lines = ContextBuilder().split_lines("改到明天下午\n\n再补一句说明\n  放到 fun 那个 list  ")
    assert lines == ["改到明天下午", "再补一句说明", "放到 fun 那个 list"]
