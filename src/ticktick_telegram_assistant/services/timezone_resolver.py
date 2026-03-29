from __future__ import annotations

from collections.abc import Mapping
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimezoneResolver:
    _ALIASES: Mapping[str, str] = {
        "北京时间": "Asia/Shanghai",
        "北京": "Asia/Shanghai",
        "上海": "Asia/Shanghai",
        "纽约": "America/New_York",
        "new york": "America/New_York",
        "nyc": "America/New_York",
        "洛杉矶": "America/Los_Angeles",
        "los angeles": "America/Los_Angeles",
        "la time": "America/Los_Angeles",
        "丹佛": "America/Denver",
        "denver": "America/Denver",
        "旧金山": "America/Los_Angeles",
        "san francisco": "America/Los_Angeles",
        "东京": "Asia/Tokyo",
        "tokyo": "Asia/Tokyo",
        "伦敦": "Europe/London",
        "london": "Europe/London",
        "太平洋时间": "America/Los_Angeles",
        "太平洋标准时间": "America/Los_Angeles",
        "太平洋夏令时间": "America/Los_Angeles",
        "东部时间": "America/New_York",
        "东部标准时间": "America/New_York",
        "东部夏令时间": "America/New_York",
        "中部时间": "America/Chicago",
        "山地时间": "America/Denver",
        "新加坡时间": "Asia/Singapore",
        "香港时间": "Asia/Hong_Kong",
        "日本时间": "Asia/Tokyo",
        "pacific time": "America/Los_Angeles",
        "pacific standard time": "America/Los_Angeles",
        "pacific daylight time": "America/Los_Angeles",
        "eastern time": "America/New_York",
        "eastern standard time": "America/New_York",
        "eastern daylight time": "America/New_York",
        "central time": "America/Chicago",
        "mountain time": "America/Denver",
        "utc": "Etc/UTC",
        "gmt": "Etc/UTC",
        "coordinated universal time": "Etc/UTC",
        "beijing time": "Asia/Shanghai",
        "shanghai time": "Asia/Shanghai",
        "hong kong time": "Asia/Hong_Kong",
        "singapore time": "Asia/Singapore",
        "japan time": "Asia/Tokyo",
        "tokyo time": "Asia/Tokyo",
    }
    _UTC_OFFSET_RE = re.compile(r"\b(?:utc|gmt)\s*(?P<sign>[+-])\s*(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?\b", re.IGNORECASE)

    async def resolve_from_location(self, *, latitude: float, longitude: float) -> str | None:
        try:
            from timezonefinder import TimezoneFinder
        except ImportError:
            return None

        finder = TimezoneFinder()
        return finder.timezone_at(lng=longitude, lat=latitude)

    def resolve_from_text(self, text: str) -> str | None:
        cleaned = text.strip()
        offset_timezone = self._resolve_utc_offset(cleaned)
        if offset_timezone is not None:
            return offset_timezone

        lowered = cleaned.casefold()
        for alias, timezone_name in self._ALIASES.items():
            if alias.casefold() in lowered:
                return timezone_name

        if "/" in cleaned and len(cleaned) < 64:
            return cleaned if self._is_valid_zone_name(cleaned) else None
        return None

    def _resolve_utc_offset(self, text: str) -> str | None:
        match = self._UTC_OFFSET_RE.search(text)
        if match is None:
            return None

        minutes = int(match.group("minutes") or "0")
        if minutes != 0:
            return None

        hours = int(match.group("hours"))
        if hours > 14:
            return None

        # Etc/GMT signs are reversed by design.
        offset_sign = "+" if match.group("sign") == "-" else "-"
        timezone_name = f"Etc/GMT{offset_sign}{hours}"
        return timezone_name if self._is_valid_zone_name(timezone_name) else None

    def _is_valid_zone_name(self, timezone_name: str) -> bool:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            return False
        return True
