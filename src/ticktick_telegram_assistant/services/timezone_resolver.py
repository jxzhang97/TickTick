from __future__ import annotations

from collections.abc import Mapping


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
    }

    async def resolve_from_location(self, *, latitude: float, longitude: float) -> str | None:
        try:
            from timezonefinder import TimezoneFinder
        except ImportError:
            return None

        finder = TimezoneFinder()
        return finder.timezone_at(lng=longitude, lat=latitude)

    def resolve_from_text(self, text: str) -> str | None:
        lowered = text.casefold()
        for alias, timezone_name in self._ALIASES.items():
            if alias.casefold() in lowered:
                return timezone_name

        cleaned = text.strip()
        if "/" in cleaned and len(cleaned) < 64:
            return cleaned
        return None
