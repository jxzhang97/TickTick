from __future__ import annotations

import json

import httpx
import pytest

from ticktick_telegram_assistant.integrations.ticktick_client import (
    TickTickChecklistItem,
    TickTickClient,
    TickTickTaskCreate,
    TickTickTaskPatch,
)


def test_ticktick_client_exposes_core_methods() -> None:
    client = TickTickClient(base_url="https://developer.ticktick.com")
    assert callable(client.list_tasks)
    assert callable(client.create_task)
    assert callable(client.update_task)
    assert callable(client.complete_task)


@pytest.mark.asyncio
async def test_ticktick_client_serializes_advanced_task_fields() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": body,
            }
        )
        if request.method == "POST" and request.url.path == "/open/v1/task":
            return httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "projectId": "inbox",
                    "title": body["title"],
                    "repeatFlag": body.get("repeatFlag"),
                    "priority": body.get("priority"),
                    "tags": body.get("tags", []),
                    "items": body.get("items", []),
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "task-1",
                "projectId": "inbox",
                "title": body.get("title", "task-1"),
                "repeatFlag": body.get("repeatFlag"),
                "priority": body.get("priority"),
                "tags": body.get("tags", []),
                "items": body.get("items", []),
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://developer.ticktick.com") as http_client:
        client = TickTickClient(base_url="https://developer.ticktick.com", http_client=http_client)

        created = await client.create_task(
            access_token="token",
            task=TickTickTaskCreate(
                title="每周同步",
                projectId="inbox",
                repeatFlag="FREQ=WEEKLY;BYDAY=MO",
                priority=3,
                tags=["work"],
                items=[TickTickChecklistItem(title="准备议程")],
            ),
        )
        updated = await client.update_task(
            access_token="token",
            task_id="task-1",
            patch=TickTickTaskPatch(
                id="task-1",
                projectId="inbox",
                repeatFlag="FREQ=WEEKLY;BYDAY=MO",
                priority=2,
                tags=["team"],
                items=[TickTickChecklistItem(title="发纪要")],
            ),
        )

    assert created.repeatFlag == "FREQ=WEEKLY;BYDAY=MO"
    assert created.priority == 3
    assert created.tags == ["work"]
    assert [item.title for item in created.items] == ["准备议程"]
    assert updated.repeatFlag == "FREQ=WEEKLY;BYDAY=MO"
    assert updated.priority == 2
    assert updated.tags == ["team"]
    assert [item.title for item in updated.items] == ["发纪要"]
    assert requests == [
        {
            "method": "POST",
            "path": "/open/v1/task",
            "body": {
                "title": "每周同步",
                "projectId": "inbox",
                "content": "",
                "desc": "",
                "reminders": [],
                "repeatFlag": "FREQ=WEEKLY;BYDAY=MO",
                "priority": 3,
                "items": [{"title": "准备议程"}],
                "tags": ["work"],
            },
        },
        {
            "method": "POST",
            "path": "/open/v1/task/task-1",
            "body": {
                "id": "task-1",
                "projectId": "inbox",
                "repeatFlag": "FREQ=WEEKLY;BYDAY=MO",
                "priority": 2,
                "items": [{"title": "发纪要"}],
                "tags": ["team"],
            },
        },
    ]
