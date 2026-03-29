from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

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
    color: Optional[str] = None
    viewMode: Optional[str] = None
    permission: Optional[str] = None
    kind: Optional[str] = None


class TickTickChecklistItem(BaseModel):
    id: Optional[str] = None
    title: str
    status: Optional[int] = None
    completedTime: Optional[str] = None
    isAllDay: Optional[bool] = None
    sortOrder: Optional[int] = None
    startDate: Optional[str] = None
    timeZone: Optional[str] = None


class TickTickTask(BaseModel):
    id: str
    projectId: str
    title: str
    content: str = ""
    desc: str = ""
    completed: bool = False
    isAllDay: Optional[bool] = None
    startDate: Optional[str] = None
    dueDate: Optional[str] = None
    timeZone: Optional[str] = None
    repeatFlag: Optional[str] = None
    reminders: list[str] = Field(default_factory=list)
    priority: Optional[int] = None
    status: Optional[int] = None
    completedTime: Optional[str] = None
    sortOrder: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    items: list[TickTickChecklistItem] = Field(default_factory=list)
    kind: Optional[str] = None


class TickTickProjectData(BaseModel):
    project: TickTickProject
    tasks: list[TickTickTask] = Field(default_factory=list)
    columns: list[dict[str, Any]] = Field(default_factory=list)


class TickTickTaskCreate(BaseModel):
    title: str
    projectId: str
    content: str = ""
    desc: str = ""
    isAllDay: Optional[bool] = None
    startDate: Optional[str] = None
    dueDate: Optional[str] = None
    timeZone: Optional[str] = None
    reminders: list[str] = Field(default_factory=list)
    repeatFlag: Optional[str] = None
    priority: Optional[int] = None
    sortOrder: Optional[int] = None
    items: list[TickTickChecklistItem] = Field(default_factory=list)
    tags: Optional[list[str]] = None


class TickTickTaskPatch(BaseModel):
    id: str
    projectId: str
    title: Optional[str] = None
    content: Optional[str] = None
    desc: Optional[str] = None
    isAllDay: Optional[bool] = None
    startDate: Optional[str] = None
    dueDate: Optional[str] = None
    timeZone: Optional[str] = None
    reminders: Optional[list[str]] = None
    repeatFlag: Optional[str] = None
    priority: Optional[int] = None
    sortOrder: Optional[int] = None
    items: Optional[list[TickTickChecklistItem]] = None
    tags: Optional[list[str]] = None


class TickTickClient:
    def __init__(
        self,
        base_url: str,
        capabilities: Optional[TickTickCapabilities] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.capabilities = capabilities or TickTickCapabilities()
        self._http_client = http_client
        self._retry_attempts = max(0, retry_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)

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

    async def list_tasks(self, *, access_token: str, since: Optional[datetime] = None) -> list[TickTickTask]:
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
    ) -> Optional[TickTickTask]:
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
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts + 1):
            try:
                response = await self._send_request(method, url, headers=headers, json=json)
                response.raise_for_status()
                return self._decode_payload(response)
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = exc
                if not self._should_retry(exc) or attempt >= self._retry_attempts:
                    raise
                await asyncio.sleep(self._retry_backoff_seconds * (2**attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("ticktick request failed without response")

    async def _send_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.request(method, url, headers=headers, json=json)

        async with httpx.AsyncClient(timeout=20.0) as client:
            return await client.request(method, url, headers=headers, json=json)

    def _should_retry(self, error: Exception) -> bool:
        if isinstance(error, httpx.RequestError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
            return 500 <= status_code < 600
        return False

    def _decode_payload(self, response: httpx.Response) -> Any:
        if not response.content:
            return None
        return response.json()
