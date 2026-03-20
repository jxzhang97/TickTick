from datetime import datetime


class ConflictDetector:
    def has_conflict(self, start_iso: str, other_iso: str) -> bool:
        start = datetime.fromisoformat(start_iso)
        other = datetime.fromisoformat(other_iso)
        return abs((start - other).total_seconds()) < 3600
