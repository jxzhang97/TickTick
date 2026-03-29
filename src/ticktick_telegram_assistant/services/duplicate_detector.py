from difflib import SequenceMatcher
import re


class DuplicateDetector:
    def is_probable_duplicate(self, left: str, right: str) -> bool:
        normalized_left = self._normalize(left)
        normalized_right = self._normalize(right)
        if not normalized_left or not normalized_right:
            return False
        if normalized_left == normalized_right:
            return True

        left_tokens = self._alnum_tokens(normalized_left)
        right_tokens = self._alnum_tokens(normalized_right)
        if left_tokens and right_tokens and left_tokens != right_tokens:
            return False

        shorter, longer = sorted((normalized_left, normalized_right), key=len)
        if shorter in longer and len(shorter) / len(longer) >= 0.7:
            return True

        char_overlap = self._char_overlap(normalized_left, normalized_right)
        sequence_ratio = SequenceMatcher(a=normalized_left, b=normalized_right).ratio()
        if sequence_ratio >= 0.8:
            return True
        if char_overlap >= 0.7 and sequence_ratio >= 0.55:
            return True
        return char_overlap >= 0.8 and min(len(normalized_left), len(normalized_right)) >= 3

    def _normalize(self, text: str) -> str:
        normalized = text.casefold()
        normalized = re.sub(r"[\\s\\-_,，。！？!?.:：;；/]+", "", normalized)
        for filler in ("提醒我", "记得", "帮我", "一下", "安排", "任务", "待办"):
            normalized = normalized.replace(filler, "")
        return normalized

    def _alnum_tokens(self, text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text))

    def _char_overlap(self, left: str, right: str) -> float:
        left_chars = set(left)
        right_chars = set(right)
        if not left_chars or not right_chars:
            return 0.0
        return len(left_chars & right_chars) / len(left_chars | right_chars)
