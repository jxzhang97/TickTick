from datetime import datetime, timedelta
import re
from typing import Any, Optional


class ReminderService:
    _RELATIVE_HOUR_RE = re.compile(r"(?P<hours>\d+)\s*小时(?:后)?")
    _TONIGHT_RE = re.compile(r"(?:今晚|今天晚上)\s*(?P<hour>\d{1,2})(?:[:：](?P<minute>\d{1,2})|点(?P<half>半)?)?")

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
        base_at = self._parse_base_time(task)
        scheduled_at = (
            self._parse_relative_snooze_time(request_text, base_at=base_at)
            or self._parse_absolute_snooze_time(request_text, base_at=base_at)
            or self._default_snooze_time(base_at)
        )
        return {
            "task_id": task["id"],
            "event_type": "snoozed_reminder",
            "scheduled_at": scheduled_at.isoformat(),
            "task_due_at": base_at.isoformat(),
        }

    def _parse_base_time(self, task: dict[str, Any]) -> datetime:
        for key in ("scheduled_at", "reminder_at", "request_at", "requested_at", "task_due_at", "due_at"):
            value = task.get(key)
            parsed = self._parse_datetime(value)
            if parsed is not None:
                return parsed
        return datetime.now().astimezone()

    def _parse_relative_snooze_time(self, request_text: str, *, base_at: datetime) -> Optional[datetime]:
        if "半小时" in request_text:
            return base_at + timedelta(minutes=30)
        match = self._RELATIVE_HOUR_RE.search(request_text)
        if match is None:
            return None
        return base_at + timedelta(hours=int(match.group("hours")))

    def _parse_absolute_snooze_time(self, request_text: str, *, base_at: datetime) -> Optional[datetime]:
        if not any(token in request_text for token in ("今晚", "今天晚上")):
            return None
        match = self._TONIGHT_RE.search(request_text)
        if match is None:
            return None
        hour = int(match.group("hour"))
        if hour < 12:
            hour += 12
        minute = 30 if match.group("half") else int(match.group("minute") or 0)
        scheduled_at = base_at.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled_at <= base_at:
            scheduled_at += timedelta(days=1)
        return scheduled_at

    def _default_snooze_time(self, base_at: datetime) -> datetime:
        return base_at + timedelta(minutes=30)

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
