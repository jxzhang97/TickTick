from __future__ import annotations

import calendar
import re
from datetime import datetime, time, timedelta

from ticktick_telegram_assistant.domain.schemas import ParsedTimeIntent


class TimeInterpreter:
    def parse(self, text: str, *, now: datetime) -> ParsedTimeIntent:
        normalized = self._normalize(text)

        explicit = self._parse_explicit_time(normalized, now=now)
        if explicit is not None:
            return explicit

        windowed = self._parse_windowed_time(normalized, now=now)
        if windowed is not None:
            return windowed

        return ParsedTimeIntent(semantic_type="memo", raw_text=text)

    def _parse_explicit_time(self, text: str, *, now: datetime) -> ParsedTimeIntent | None:
        match = re.search(
            r"(?P<day>今晚|今天|明天|后天)(?P<meridiem>上午|中午|下午|晚上)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?点",
            text,
        )
        if match is None:
            return None

        day_token = match.group("day")
        day_offset = {"今天": 0, "今晚": 0, "明天": 1, "后天": 2}[day_token]
        base_date = (now + timedelta(days=day_offset)).date()
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)

        meridiem = match.group("meridiem") or ""
        if meridiem in {"下午", "晚上"} and hour < 12:
            hour += 12
        elif meridiem == "中午" and hour < 12:
            hour = 12 if hour == 0 else hour
        elif day_token == "今晚" and hour < 12:
            hour += 12

        due_at = datetime.combine(base_date, time(hour=hour, minute=minute), tzinfo=now.tzinfo)
        return ParsedTimeIntent(semantic_type="explicit_time", due_at=due_at, raw_text=text)

    def _parse_windowed_time(self, text: str, *, now: datetime) -> ParsedTimeIntent | None:
        if "下周" in text:
            return self._parse_next_week(now=now, raw_text=text)
        if "这两周" in text:
            return self._parse_two_weeks(now=now, raw_text=text)
        if "月底前" in text:
            return self._parse_month_end(now=now, raw_text=text)
        if "明天下午" in text:
            return self._parse_day_part_window(
                raw_text=text,
                now=now,
                day_offset=1,
                start_hour=12,
                end_hour=18,
            )
        if "今晚" in text:
            return self._parse_day_part_window(
                raw_text=text,
                now=now,
                day_offset=0,
                start_hour=18,
                end_hour=24,
            )
        return None

    def _parse_next_week(self, *, now: datetime, raw_text: str) -> ParsedTimeIntent:
        days_until_next_monday = (7 - now.weekday()) or 7
        window_start = (now + timedelta(days=days_until_next_monday)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        window_end = window_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return ParsedTimeIntent(
            semantic_type="windowed",
            window_start=window_start,
            window_end=window_end,
            raw_text=raw_text,
        )

    def _parse_two_weeks(self, *, now: datetime, raw_text: str) -> ParsedTimeIntent:
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = window_start + timedelta(days=13, hours=23, minutes=59, seconds=59)
        return ParsedTimeIntent(
            semantic_type="windowed",
            window_start=window_start,
            window_end=window_end,
            raw_text=raw_text,
        )

    def _parse_month_end(self, *, now: datetime, raw_text: str) -> ParsedTimeIntent:
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        window_end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)
        return ParsedTimeIntent(
            semantic_type="windowed",
            window_start=window_start,
            window_end=window_end,
            raw_text=raw_text,
        )

    def _parse_day_part_window(
        self,
        *,
        raw_text: str,
        now: datetime,
        day_offset: int,
        start_hour: int,
        end_hour: int,
    ) -> ParsedTimeIntent:
        base_date = (now + timedelta(days=day_offset)).date()
        window_start = datetime.combine(base_date, time(hour=start_hour), tzinfo=now.tzinfo)
        if end_hour == 24:
            window_end = datetime.combine(base_date, time(hour=23, minute=59, second=59), tzinfo=now.tzinfo)
        else:
            window_end = datetime.combine(base_date, time(hour=end_hour), tzinfo=now.tzinfo)
        return ParsedTimeIntent(
            semantic_type="windowed",
            window_start=window_start,
            window_end=window_end,
            raw_text=raw_text,
        )

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", "", text)
