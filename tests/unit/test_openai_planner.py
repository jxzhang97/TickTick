from __future__ import annotations

import pytest

from ticktick_telegram_assistant.domain.schemas import ConversationContext
from ticktick_telegram_assistant.integrations.openai_planner import OpenAIPlanner


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeResponsesAPI:
    def __init__(self, output_text: str) -> None:
        self._output_text = output_text
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self._output_text)


class FakeOpenAIClient:
    def __init__(self, output_text: str) -> None:
        self.responses = FakeResponsesAPI(output_text)


@pytest.mark.asyncio
async def test_plan_returns_structured_actions_from_json_response() -> None:
    client = FakeOpenAIClient(
        """
        {
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

    planned = await planner.plan(
        ConversationContext(
            user_text="明天下午3点提醒我给导师发邮件",
            current_timezone="America/Los_Angeles",
            current_local_time="2026-03-27T09:00:00-07:00",
        )
    )

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
    assert "complete_task" in prompt
    assert "update_task" in prompt
    assert "end_at" in prompt
    assert "duration_minutes" in prompt
    assert "repeat_rule" in prompt
    assert "priority" in prompt
    assert "tags" in prompt
    assert "checklist" in prompt


@pytest.mark.asyncio
async def test_plan_returns_empty_plan_when_model_output_is_invalid() -> None:
    planner = OpenAIPlanner(client=FakeOpenAIClient("not json"))

    planned = await planner.plan(
        ConversationContext(
            user_text="随便说一句",
            current_timezone="America/Los_Angeles",
            current_local_time="2026-03-27T09:00:00-07:00",
        )
    )

    assert planned.actions == []
    assert planned.assistant_reply is None
