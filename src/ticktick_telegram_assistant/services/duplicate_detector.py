from difflib import SequenceMatcher


class DuplicateDetector:
    def is_probable_duplicate(self, left: str, right: str) -> bool:
        return SequenceMatcher(a=left, b=right).ratio() >= 0.5

