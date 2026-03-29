from __future__ import annotations

import asyncio

from ticktick_telegram_assistant.domain.schemas import ConversationContext
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeResponsesAPI:
    def __init__(self, output_text: str, *, error: Exception | None = None) -> None:
        self._output_text = output_text
        self._error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return FakeResponse(self._output_text)


class FakeOpenAIClient:
    def __init__(self, output_text: str, *, error: Exception | None = None) -> None:
        self.responses = FakeResponsesAPI(output_text, error=error)


def test_plan_returns_structured_actions_from_json_response() -> None:
    client = FakeOpenAIClient(
        """
        {
          "intent_type": "task_write",
          "task_write": {
            "write_type": "create",
            "target_title": "给导师发邮件",
            "summary": "明天下午3点提醒我给导师发邮件"
          },
          "actions": [
            {
              "action_type": "create_task",
              "payload": {
                "title": "给导师发邮件",
                "semantic_type": "explicit_time",
                "due_at": "2026-03-28T15:00:00-07:00",
                "end_at": "2026-03-28T16:00:00-07:00",
                "duration_minutes": 60,
                "repeat_rule": "FREQ=WEEKLY;BYDAY=MO",
                "priority": 3,
                "tags": ["work"],
                "checklist": ["发邮件", "确认收件"]
              }
            }
          ],
          "requires_confirmation": false,
          "assistant_reply": null
        }
        """
    )
    planner = OpenAIPlanner(client=client)

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="明天下午3点提醒我给导师发邮件",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.intent_type == "task_write"
    assert planned.task_write is not None
    assert planned.task_write.write_type == "create"
    assert planned.task_write.target_title == "给导师发邮件"
    assert planned.actions[0].action_type == "create_task"
    assert planned.actions[0].payload["title"] == "给导师发邮件"
    assert planned.actions[0].payload["repeat_rule"] == "FREQ=WEEKLY;BYDAY=MO"
    assert planned.actions[0].payload["end_at"] == "2026-03-28T16:00:00-07:00"
    assert planned.actions[0].payload["duration_minutes"] == 60
    assert planned.actions[0].payload["priority"] == 3
    assert planned.actions[0].payload["tags"] == ["work"]
    assert planned.actions[0].payload["checklist"] == ["发邮件", "确认收件"]
    prompt = client.responses.calls[0]["input"]
    assert "JSON" in prompt
    assert "明天下午3点提醒我给导师发邮件" in prompt
    assert "2026-03-27T09:00:00-07:00" in prompt
    assert "intent_type" in prompt
    assert "query" in prompt
    assert "reminder_control" in prompt
    assert "clarification" in prompt
    assert "task_write" in prompt
    assert "complete_task" in prompt
    assert "update_task" in prompt
    assert "end_at" in prompt
    assert "duration_minutes" in prompt
    assert "repeat_rule" in prompt
    assert "priority" in prompt
    assert "tags" in prompt
    assert "checklist" in prompt


def test_plan_parses_query_today_brief_intent() -> None:
    planner = OpenAIPlanner(
        client=FakeOpenAIClient(
            """
            {
              "intent_type": "query",
              "query": {
                "query_kind": "today_brief",
                "query_text": "今天有什么安排",
                "time_scope": "today"
              },
              "actions": [],
              "requires_confirmation": false,
              "assistant_reply": "今天我先帮你看安排。"
            }
            """
        )
    )

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="今天有什么安排",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.intent_type == "query"
    assert planned.query is not None
    assert planned.query.query_kind == "today_brief"
    assert planned.query.query_text == "今天有什么安排"
    assert planned.query.time_scope == "today"
    assert planned.actions == []
    assert planned.assistant_reply == "今天我先帮你看安排。"


def test_plan_parses_recent_query_intent_with_custom_range_fields() -> None:
    client = FakeOpenAIClient(
        """
        {
          "intent_type": "query",
          "query": {
            "query_kind": "schedule_query",
            "query_text": "我最近有什么事",
            "time_scope": "recent",
            "range_start": "2026-03-27T00:00:00-07:00",
            "range_end": "2026-04-03T23:59:00-07:00"
          },
          "actions": [],
          "requires_confirmation": false,
          "assistant_reply": null
        }
        """
    )
    planner = OpenAIPlanner(client=client)

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="我最近有什么事",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
                recent_conversation_summaries=["最近在安排 talk 和 ddl"],
            )
        )
    )

    assert planned.intent_type == "query"
    assert planned.query is not None
    assert planned.query.query_kind == "schedule_query"
    assert planned.query.query_text == "我最近有什么事"
    assert planned.query.time_scope == "recent"
    assert planned.query.range_start.isoformat() == "2026-03-27T00:00:00-07:00"
    assert planned.query.range_end.isoformat() == "2026-04-03T23:59:00-07:00"
    prompt = client.responses.calls[0]["input"]
    assert "recent|upcoming|overdue|this_week|custom_range" in prompt
    assert "range_start" in prompt
    assert "range_end" in prompt
    assert "今天要干嘛" in prompt
    assert "我最近有什么事" in prompt
    assert "最近在安排 talk 和 ddl" in prompt


def test_plan_parses_upcoming_query_intent() -> None:
    planner = OpenAIPlanner(
        client=FakeOpenAIClient(
            """
            {
              "intent_type": "query",
              "query": {
                "query_kind": "schedule_query",
                "query_text": "未来几天有什么安排",
                "time_scope": "upcoming"
              },
              "actions": [],
              "requires_confirmation": false,
              "assistant_reply": null
            }
            """
        )
    )

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="未来几天有什么安排",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.query is not None
    assert planned.query.time_scope == "upcoming"


def test_plan_parses_overdue_query_intent() -> None:
    planner = OpenAIPlanner(
        client=FakeOpenAIClient(
            """
            {
              "intent_type": "query",
              "query": {
                "query_kind": "overdue_review",
                "query_text": "我手上还挂着什么",
                "time_scope": "overdue"
              },
              "actions": [],
              "requires_confirmation": false,
              "assistant_reply": null
            }
            """
        )
    )

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="我手上还挂着什么",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.query is not None
    assert planned.query.time_scope == "overdue"


def test_plan_parses_this_week_query_intent() -> None:
    planner = OpenAIPlanner(
        client=FakeOpenAIClient(
            """
            {
              "intent_type": "query",
              "query": {
                "query_kind": "schedule_query",
                "query_text": "这周有什么安排",
                "time_scope": "this_week"
              },
              "actions": [],
              "requires_confirmation": false,
              "assistant_reply": null
            }
            """
        )
    )

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="这周有什么安排",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.query is not None
    assert planned.query.time_scope == "this_week"


def test_plan_parses_custom_range_query_intent() -> None:
    planner = OpenAIPlanner(
        client=FakeOpenAIClient(
            """
            {
              "intent_type": "query",
              "query": {
                "query_kind": "schedule_query",
                "query_text": "4 月 1 日到 4 月 3 日我有什么事",
                "time_scope": "custom_range",
                "range_start": "2026-04-01T00:00:00-07:00",
                "range_end": "2026-04-03T23:59:00-07:00"
              },
              "actions": [],
              "requires_confirmation": false,
              "assistant_reply": null
            }
            """
        )
    )

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="4 月 1 日到 4 月 3 日我有什么事",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.query is not None
    assert planned.query.time_scope == "custom_range"
    assert planned.query.range_start.isoformat() == "2026-04-01T00:00:00-07:00"
    assert planned.query.range_end.isoformat() == "2026-04-03T23:59:00-07:00"


def test_plan_parses_clarification_intent() -> None:
    planner = OpenAIPlanner(
        client=FakeOpenAIClient(
            """
            {
              "intent_type": "clarification",
              "clarification": {
                "question": "你是想先别催，还是延后 1 小时再提醒？",
                "options": ["先别催", "延后 1 小时"]
              },
              "requires_confirmation": true,
              "assistant_reply": "你想怎么处理这条提醒？"
            }
            """
        )
    )

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="先别催我",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.intent_type == "clarification"
    assert planned.clarification is not None
    assert planned.clarification.question == "你是想先别催，还是延后 1 小时再提醒？"
    assert planned.clarification.options == ["先别催", "延后 1 小时"]
    assert planned.requires_confirmation is True
    assert planned.actions == []
    assert planned.assistant_reply == "你想怎么处理这条提醒？"


def test_plan_parses_reminder_control_intent() -> None:
    planner = OpenAIPlanner(
        client=FakeOpenAIClient(
            """
            {
              "intent_type": "reminder_control",
              "reminder_control": {
                "control_type": "snooze",
                "delay_minutes": 60,
                "scope": "telegram_reminder"
              },
              "actions": [],
              "requires_confirmation": false,
              "assistant_reply": "我先帮你把提醒往后挪 1 小时。"
            }
            """
        )
    )

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="1小时后再提醒我",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.intent_type == "reminder_control"
    assert planned.reminder_control is not None
    assert planned.reminder_control.control_type == "snooze"
    assert planned.reminder_control.delay_minutes == 60
    assert planned.reminder_control.scope == "telegram_reminder"
    assert planned.actions == []
    assert planned.assistant_reply == "我先帮你把提醒往后挪 1 小时。"


def test_plan_returns_empty_plan_when_model_output_is_invalid() -> None:
    planner = OpenAIPlanner(client=FakeOpenAIClient("not json"))

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="随便说一句",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.actions == []
    assert planned.assistant_reply is None


def test_plan_returns_empty_plan_when_openai_client_raises() -> None:
    planner = OpenAIPlanner(client=FakeOpenAIClient("", error=RuntimeError("boom")))

    planned = asyncio.run(
        planner.plan(
            ConversationContext(
                user_text="随便说一句",
                current_timezone="America/Los_Angeles",
                current_local_time="2026-03-27T09:00:00-07:00",
            )
        )
    )

    assert planned.actions == []
    assert planned.assistant_reply is None
