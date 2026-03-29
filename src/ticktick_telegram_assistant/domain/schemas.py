from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TickTickTaskRef(BaseModel):
    task_id: str
    title: Optional[str] = None


class ParsedTimeIntent(BaseModel):
    semantic_type: str
    due_at: Optional[datetime] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    raw_text: str


class ConversationContext(BaseModel):
    user_text: str
    current_timezone: str = "America/Los_Angeles"
    current_local_time: Optional[str] = None
    memory_items: list[str] = Field(default_factory=list)
    recent_conversation_summaries: list[str] = Field(default_factory=list)
    candidate_tasks: list[str] = Field(default_factory=list)

    def to_prompt(self) -> str:
        return self.model_dump_json()


class PlannedConversation(BaseModel):
    intent_type: Optional[Literal["query", "reminder_control", "clarification", "task_write"]] = None
    query: Optional["PlannedQueryIntent"] = None
    reminder_control: Optional["PlannedReminderControlIntent"] = None
    clarification: Optional["PlannedClarificationIntent"] = None
    task_write: Optional["PlannedTaskWriteIntent"] = None
    actions: list["PlannedAction"] = Field(default_factory=list)
    requires_confirmation: bool = False
    assistant_reply: Optional[str] = None


class PlannedQueryIntent(BaseModel):
    query_kind: str
    query_text: Optional[str] = None
    time_scope: Optional[str] = None
    target_task_id: Optional[str] = None
    target_title: Optional[str] = None


class PlannedReminderControlIntent(BaseModel):
    control_type: str
    delay_minutes: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    target_task_id: Optional[str] = None
    target_title: Optional[str] = None
    scope: Optional[str] = None


class PlannedClarificationIntent(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)
    reason: Optional[str] = None


class PlannedTaskWriteIntent(BaseModel):
    write_type: Literal["create", "update", "complete"]
    target_title: Optional[str] = None
    target_task_id: Optional[str] = None
    summary: Optional[str] = None


class PlannedAction(BaseModel):
    action_type: str
    target_task_id: Optional[str] = None
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


class TelegramReply(BaseModel):
    chat_id: int
    text: str


class TickTickOAuthConnectionResult(BaseModel):
    connected: bool
    message: str


try:  # pydantic v2
    PlannedConversation.model_rebuild()
except AttributeError:  # pydantic v1
    PlannedConversation.update_forward_refs(
        PlannedAction=PlannedAction,
        PlannedQueryIntent=PlannedQueryIntent,
        PlannedReminderControlIntent=PlannedReminderControlIntent,
        PlannedClarificationIntent=PlannedClarificationIntent,
        PlannedTaskWriteIntent=PlannedTaskWriteIntent,
    )
