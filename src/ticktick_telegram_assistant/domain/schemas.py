from typing import Any

from pydantic import BaseModel, Field


class PlannedAction(BaseModel):
    action_type: str
    target_task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ConfirmationRequest(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class ReminderEventPayload(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class BriefingSection(BaseModel):
    title: str
    items: list[str] = Field(default_factory=list)

