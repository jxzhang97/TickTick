import re
from datetime import datetime, timedelta
from typing import Any, Optional


class ReminderService:
    _RELATIVE_HOUR_RE = re.compile(r"(?P<hours>\d+)\s*小时(?:后)?")
    _ABSOLUTE_SNOOZE_RE = re.compile(
        r"(?P<day>今天晚上|今晚|今天|明天|后天)"
        r"(?P<period>早上|上午|中午|下午|傍晚|晚上|凌晨)?"
        r"\s*(?:(?P<hour>\d{1,2})(?:[:：](?P<minute>\d{1,2})|点(?P<half>半)?)?)?"
    )
    _DAY_OFFSETS = {
        "今天": 0,
        "今天晚上": 0,
        "今晚": 0,
        "明天": 1,
        "后天": 2,
    }
    _PERIOD_DEFAULTS = {
        "早上": (8, 0),
        "上午": (9, 0),
        "中午": (12, 0),
        "下午": (15, 0),
        "傍晚": (18, 0),
        "晚上": (20, 0),
        "凌晨": (1, 0),
        "今晚": (20, 0),
        "今天晚上": (20, 0),
    }

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
        match = self._ABSOLUTE_SNOOZE_RE.search(request_text)
        if match is None:
            return None
        day_token = match.group("day")
        period_token = match.group("period") or ""
        hour_text = match.group("hour")
        minute_text = match.group("minute")
        half_text = match.group("half")

        hour, minute = self._resolve_absolute_time_components(
            day_token=day_token,
            period_token=period_token,
            hour_text=hour_text,
            minute_text=minute_text,
            half_text=half_text,
        )
        scheduled_at = base_at.replace(hour=hour, minute=minute, second=0, microsecond=0)
        scheduled_at += timedelta(days=self._DAY_OFFSETS[day_token])
        if scheduled_at <= base_at:
            scheduled_at += timedelta(days=1)
        return scheduled_at

    def _resolve_absolute_time_components(
        self,
        *,
        day_token: str,
        period_token: str,
        hour_text: str | None,
        minute_text: str | None,
        half_text: str | None,
    ) -> tuple[int, int]:
        time_context = period_token or ("今晚" if day_token in {"今晚", "今天晚上"} else "")
        default_hour, default_minute = self._PERIOD_DEFAULTS.get(time_context or day_token, (8, 0))
        if hour_text is None:
            return default_hour, default_minute

        hour = int(hour_text)
        minute = 30 if half_text else int(minute_text or 0)

        if time_context in {"下午", "傍晚", "晚上", "今晚", "今天晚上"} and hour < 12:
            hour += 12
        elif time_context == "中午" and hour < 12:
            hour += 12
        elif time_context == "凌晨" and hour == 12:
            hour = 0
        return hour, minute

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
