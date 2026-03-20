from datetime import datetime

from pydantic import BaseModel, Field


class UnsupportedTickTickCapability(RuntimeError):
    pass


class TickTickCapabilities(BaseModel):
    supports_repeat_rules: bool = True
    supports_multiple_reminders: bool = True
    supports_notes: bool = True


class TickTickTask(BaseModel):
    id: str
    title: str
    description: str = ""
    completed: bool = False


class TickTickTaskCreate(BaseModel):
    title: str
    description: str = ""


class TickTickTaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    completed: bool | None = None


class TickTickClient:
    def __init__(self, base_url: str, capabilities: TickTickCapabilities | None = None) -> None:
        self.base_url = base_url
        self.capabilities = capabilities or TickTickCapabilities()

    async def list_tasks(self, *, since: datetime | None = None) -> list[TickTickTask]:
        return []

    async def create_task(self, task: TickTickTaskCreate) -> TickTickTask:
        return TickTickTask(id="pending", title=task.title, description=task.description)

    async def update_task(self, task_id: str, patch: TickTickTaskPatch) -> TickTickTask:
        return TickTickTask(
            id=task_id,
            title=patch.title or "",
            description=patch.description or "",
            completed=bool(patch.completed),
        )

    async def complete_task(self, task_id: str) -> TickTickTask:
        return TickTickTask(id=task_id, title="", description="", completed=True)
