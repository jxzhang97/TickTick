from __future__ import annotations

import httpx
import pytest

from ticktick_telegram_assistant.integrations.ticktick_client import TickTickClient, TickTickTaskPatch


@pytest.mark.asyncio
async def test_list_projects_and_project_data_use_official_open_api_paths() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/open/v1/project":
            return httpx.Response(
                200,
                json=[{"id": "p1", "name": "Inbox", "kind": "TASK"}],
            )
        if request.url.path == "/open/v1/project/p1/data":
            return httpx.Response(
                200,
                json={
                    "project": {"id": "p1", "name": "Inbox", "kind": "TASK"},
                    "tasks": [{"id": "t1", "projectId": "p1", "title": "Task"}],
                    "columns": [],
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    client = TickTickClient(
        base_url="https://api.ticktick.com",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    projects = await client.list_projects(access_token="token-123")
    project_data = await client.get_project_data(project_id="p1", access_token="token-123")

    assert [project.id for project in projects] == ["p1"]
    assert project_data.tasks[0].id == "t1"
    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer token-123",
        "Bearer token-123",
    ]
    assert [request.url.path for request in requests] == [
        "/open/v1/project",
        "/open/v1/project/p1/data",
    ]


@pytest.mark.asyncio
async def test_update_task_accepts_empty_response_body() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"")

    client = TickTickClient(
        base_url="https://api.ticktick.com",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await client.update_task(
        access_token="token-123",
        task_id="t1",
        patch=TickTickTaskPatch(
            id="t1",
            projectId="p1",
            desc="记得带附件",
        ),
    )

    assert result is None
    assert [request.url.path for request in requests] == ["/open/v1/task/t1"]
