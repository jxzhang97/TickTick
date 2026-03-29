from datetime import datetime, timedelta


class ConflictDetector:
    def has_conflict(
        self,
        start_iso: str,
        other_iso: str,
        *,
        end_iso: str | None = None,
        other_end_iso: str | None = None,
    ) -> bool:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso) if end_iso else None
        other = datetime.fromisoformat(other_iso)
        other_end = datetime.fromisoformat(other_end_iso) if other_end_iso else None

        if end is None and other_end is None:
            return abs((start - other).total_seconds()) < 3600

        if end is None or end <= start:
            end = start + timedelta(hours=1)
        if other_end is None or other_end <= other:
            other_end = other + timedelta(hours=1)
        return max(start, other) < min(end, other_end)
