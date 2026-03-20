from datetime import datetime, timedelta
from typing import Any


class ReminderService:
    def build_events(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        semantic_type = task.get("semantic_type")
        if semantic_type == "explicit_time" and task.get("due_at"):
            due_at = datetime.fromisoformat(task["due_at"])
            return [
                {
                    "task_id": task["id"],
                    "event_type": "prestart_reminder",
                    "scheduled_at": (due_at - timedelta(minutes=5)).isoformat(),
                }
            ]
        if semantic_type == "windowed" and task.get("window_start"):
            return [
                {
                    "task_id": task["id"],
                    "event_type": "window_start_ping",
                    "scheduled_at": task["window_start"],
                }
            ]
        return []

    def build_snooze_event(self, task: dict[str, Any], request_text: str) -> dict[str, Any]:
        due_at = datetime.fromisoformat(task["due_at"])
        if "1小时" in request_text:
            scheduled_at = due_at + timedelta(hours=1)
        else:
            scheduled_at = due_at + timedelta(minutes=30)
        return {
            "task_id": task["id"],
            "event_type": "snoozed_reminder",
            "scheduled_at": scheduled_at.isoformat(),
            "task_due_at": task["due_at"],
        }
