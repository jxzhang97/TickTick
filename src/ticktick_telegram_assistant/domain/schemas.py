from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TickTickTaskRef(BaseModel):
    task_id: str
    title: str | None = None


class ParsedTimeIntent(BaseModel):
    semantic_type: str
    due_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    raw_text: str


class ConversationContext(BaseModel):
    user_text: str
    current_timezone: str = "America/Los_Angeles"
    memory_items: list[str] = Field(default_factory=list)
    candidate_tasks: list[str] = Field(default_factory=list)

    def to_prompt(self) -> str:
        return self.model_dump_json()


class PlannedConversation(BaseModel):
    actions: list["PlannedAction"] = Field(default_factory=list)
    requires_confirmation: bool = False


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


class EveningReviewReply(BaseModel):
    completed_indices: list[int] = Field(default_factory=list)
    rescheduled_indices: list[int] = Field(default_factory=list)
