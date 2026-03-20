from datetime import datetime, time, timedelta

from ticktick_telegram_assistant.domain.schemas import ParsedTimeIntent


class TimeInterpreter:
    def parse(self, text: str, *, now: datetime) -> ParsedTimeIntent:
        if "下周" in text:
            return self._parse_next_week(text, now=now)
        return self._parse_explicit_or_memo(text, now=now)

    def _parse_next_week(self, text: str, *, now: datetime) -> ParsedTimeIntent:
        days_until_next_monday = (7 - now.weekday()) or 7
        window_start = (now + timedelta(days=days_until_next_monday)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        window_end = window_start + timedelta(days=6, hours=23, minutes=59)
        return ParsedTimeIntent(
            semantic_type="windowed",
            window_start=window_start,
            window_end=window_end,
            raw_text=text,
        )

    def _parse_explicit_or_memo(self, text: str, *, now: datetime) -> ParsedTimeIntent:
        if "明天" in text and "3点" in text:
            due_at = datetime.combine(
                (now + timedelta(days=1)).date(),
                time(hour=15),
                tzinfo=now.tzinfo,
            )
            return ParsedTimeIntent(
                semantic_type="explicit_time",
                due_at=due_at,
                raw_text=text,
            )
        return ParsedTimeIntent(semantic_type="memo", raw_text=text)

