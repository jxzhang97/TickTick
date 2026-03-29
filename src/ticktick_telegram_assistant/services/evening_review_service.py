from __future__ import annotations

import re

from ticktick_telegram_assistant.domain.schemas import EveningReviewReply


class EveningReviewService:
    _COMPLETION_VERBS = ("做完", "完成", "搞定")
    _RESCHEDULE_VERBS = ("改到", "改成", "挪到", "推到", "顺延到", "延期到", "放到")
    _ALL_DONE_PHRASES = ("都做完", "全部做完", "全做完", "都完成", "全部完成", "全完成", "都搞定", "全部搞定")

    def parse_reply(self, *, reply_text: str, candidate_titles: list[str]) -> EveningReviewReply:
        normalized = self._normalize(reply_text)
        completed_indices: list[int] = []
        rescheduled_indices: list[int] = []

        completed_indices.extend(self._parse_completed_indices(normalized, candidate_count=len(candidate_titles)))
        rescheduled_indices.extend(self._parse_rescheduled_indices(normalized, candidate_count=len(candidate_titles)))

        completed_indices = self._dedupe_preserve_order(completed_indices)
        rescheduled_indices = [
            index for index in self._dedupe_preserve_order(rescheduled_indices) if index not in completed_indices
        ]

        return EveningReviewReply(
            completed_indices=completed_indices,
            rescheduled_indices=rescheduled_indices,
        )

    def _parse_completed_indices(self, text: str, *, candidate_count: int) -> list[int]:
        if candidate_count <= 0:
            return []
        if any(phrase in text for phrase in self._ALL_DONE_PHRASES) and not any(
            marker in text for marker in ("前", "第", "除了", "最后")
        ):
            return list(range(candidate_count))

        completion_prefix = self._prefix_before_first(text, self._COMPLETION_VERBS)
        if completion_prefix is None:
            return []

        if "除了" in completion_prefix:
            exclusion_segment = completion_prefix.split("除了", 1)[1]
            exclusion_segment = re.split(r"[，,。；;]|都", exclusion_segment, maxsplit=1)[0]
            excluded = set(self._extract_indices(exclusion_segment, candidate_count=candidate_count))
            if excluded:
                return [index for index in range(candidate_count) if index not in excluded]
            return []

        front_count = self._extract_front_count(completion_prefix, candidate_count=candidate_count)
        if front_count is not None:
            return list(range(front_count))

        return self._extract_indices(completion_prefix, candidate_count=candidate_count)

    def _parse_rescheduled_indices(self, text: str, *, candidate_count: int) -> list[int]:
        indices: list[int] = []
        for verb in self._RESCHEDULE_VERBS:
            for match in re.finditer(re.escape(verb), text):
                prefix = text[: match.start()]
                clause = self._trailing_clause(prefix)
                front_count = self._extract_front_count(clause, candidate_count=candidate_count)
                if front_count is not None:
                    indices.extend(range(front_count))
                    continue
                indices.extend(self._extract_indices(clause, candidate_count=candidate_count))
        return indices

    def _prefix_before_first(self, text: str, verbs: tuple[str, ...]) -> str | None:
        positions = [text.find(verb) for verb in verbs if text.find(verb) != -1]
        if not positions:
            return None
        return text[: min(positions)]

    def _extract_front_count(self, text: str, *, candidate_count: int) -> int | None:
        match = re.search(r"前(?:面)?(?P<count>[一二两三四五六七八九十\d]+)(?:个|项|条|件)?", text)
        if match:
            count = self._parse_ordinal_token(match.group("count"))
            if count is not None:
                return min(count, candidate_count)
        return None

    def _extract_indices(self, text: str, *, candidate_count: int) -> list[int]:
        indices: list[int] = []

        if "最后一个" in text or "最后一项" in text or "最后一条" in text:
            if candidate_count > 0:
                indices.append(candidate_count - 1)

        for match in re.finditer(r"(?:第)?(?P<count>[一二两三四五六七八九十\d]+)(?:个|项|条|件)?", text):
            count = self._parse_ordinal_token(match.group("count"))
            if count is None or count <= 0:
                continue
            index = count - 1
            if 0 <= index < candidate_count:
                indices.append(index)
        return indices

    def _parse_ordinal_token(self, token: str) -> int | None:
        if token.isdigit():
            return int(token)

        mapping = {
            "零": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        if token in mapping:
            return mapping[token]
        if "十" not in token:
            return None

        left, right = token.split("十", 1)
        tens = 1 if left == "" else mapping.get(left)
        ones = 0 if right == "" else mapping.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", "", text)

    def _trailing_clause(self, text: str) -> str:
        clauses = re.split(r"[，,。；;]|做完了|完成了|搞定了", text)
        return clauses[-1] if clauses else text

    def _dedupe_preserve_order(self, indices: list[int]) -> list[int]:
        seen: set[int] = set()
        deduped: list[int] = []
        for index in indices:
            if index in seen:
                continue
            seen.add(index)
            deduped.append(index)
        return deduped
