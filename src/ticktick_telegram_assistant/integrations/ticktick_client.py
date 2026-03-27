from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field


class UnsupportedTickTickCapability(RuntimeError):
    pass


class TickTickCapabilities(BaseModel):
    supports_repeat_rules: bool = True
    supports_multiple_reminders: bool = True
    supports_notes: bool = True


class TickTickProject(BaseModel):
    id: str
    name: str
    color: str | None = None
    viewMode: str | None = None
    permission: str | None = None
    kind: str | None = None


class TickTickChecklistItem(BaseModel):
    id: str | None = None
    title: str
    status: int | None = None
    completedTime: str | None = None
    isAllDay: bool | None = None
    sortOrder: int | None = None
    startDate: str | None = None
    timeZone: str | None = None


class TickTickTask(BaseModel):
    id: str
    projectId: str
    title: str
    content: str = ""
    desc: str = ""
    completed: bool = False
    isAllDay: bool | None = None
    startDate: str | None = None
    dueDate: str | None = None
    timeZone: str | None = None
    repeatFlag: str | None = None
    reminders: list[str] = Field(default_factory=list)
    priority: int | None = None
    status: int | None = None
    completedTime: str | None = None
    sortOrder: int | None = None
    tags: list[str] = Field(default_factory=list)
    items: list[TickTickChecklistItem] = Field(default_factory=list)
    kind: str | None = None


class TickTickProjectData(BaseModel):
    project: TickTickProject
    tasks: list[TickTickTask] = Field(default_factory=list)
    columns: list[dict[str, Any]] = Field(default_factory=list)


class TickTickTaskCreate(BaseModel):
    title: str
    projectId: str
    content: str = ""
    desc: str = ""
    isAllDay: bool | None = None
    startDate: str | None = None
    dueDate: str | None = None
    timeZone: str | None = None
    reminders: list[str] = Field(default_factory=list)
    repeatFlag: str | None = None
    priority: int | None = None
    sortOrder: int | None = None
    items: list[TickTickChecklistItem] = Field(default_factory=list)


class TickTickTaskPatch(BaseModel):
    id: str
    projectId: str
    title: str | None = None
    content: str | None = None
    desc: str | None = None
    isAllDay: bool | None = None
    startDate: str | None = None
    dueDate: str | None = None
    timeZone: str | None = None
    reminders: list[str] | None = None
    repeatFlag: str | None = None
    priority: int | None = None
    sortOrder: int | None = None
    items: list[TickTickChecklistItem] | None = None


class TickTickClient:
    def __init__(
        self,
        base_url: str,
        capabilities: TickTickCapabilities | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.capabilities = capabilities or TickTickCapabilities()
        self._http_client = http_client

    async def list_projects(self, *, access_token: str) -> list[TickTickProject]:
        payload = await self._request("GET", "/open/v1/project", access_token=access_token)
        return [TickTickProject.model_validate(item) for item in payload]

    async def get_project_data(self, *, project_id: str, access_token: str) -> TickTickProjectData:
        payload = await self._request(
            "GET",
            f"/open/v1/project/{project_id}/data",
            access_token=access_token,
        )
        return TickTickProjectData.model_validate(payload)

    async def list_tasks(self, *, access_token: str, since: datetime | None = None) -> list[TickTickTask]:
        projects = await self.list_projects(access_token=access_token)
        tasks: list[TickTickTask] = []
        for project in projects:
            project_data = await self.get_project_data(project_id=project.id, access_token=access_token)
            tasks.extend(project_data.tasks)
        return tasks

    async def create_task(self, *, access_token: str, task: TickTickTaskCreate) -> TickTickTask:
        payload = await self._request(
            "POST",
            "/open/v1/task",
            access_token=access_token,
            json=task.model_dump(exclude_none=True),
        )
        return TickTickTask.model_validate(payload)

    async def update_task(
        self,
        *,
        access_token: str,
        task_id: str,
        patch: TickTickTaskPatch,
    ) -> TickTickTask | None:
        payload = await self._request(
            "POST",
            f"/open/v1/task/{task_id}",
            access_token=access_token,
            json=patch.model_dump(exclude_none=True),
        )
        if payload is None:
            return None
        return TickTickTask.model_validate(payload)

    async def complete_task(self, *, access_token: str, project_id: str, task_id: str) -> None:
        await self._request(
            "POST",
            f"/open/v1/project/{project_id}/task/{task_id}/complete",
            access_token=access_token,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        json: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{self.base_url}{path}"
        if self._http_client is not None:
            response = await self._http_client.request(method, url, headers=headers, json=json)
            response.raise_for_status()
            return self._decode_payload(response)

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(method, url, headers=headers, json=json)
            response.raise_for_status()
            return self._decode_payload(response)

    def _decode_payload(self, response: httpx.Response) -> Any:
        if not response.content:
            return None
        return response.json()
