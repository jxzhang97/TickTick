from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.active_context import ActiveContext
from ticktick_telegram_assistant.db.models.conversation_summary import ConversationSummary
from ticktick_telegram_assistant.db.models.memory_fact import MemoryFact
from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.domain.schemas import ConversationContext
from ticktick_telegram_assistant.repositories.contexts import ContextRepository
from ticktick_telegram_assistant.repositories.memory import MemoryRepository
from ticktick_telegram_assistant.services.context_builder import ContextBuilder


class MemoryService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._context_builder = context_builder or ContextBuilder()

    def upsert_fact(
        self,
        *,
        user_id: int,
        key: str,
        value_json: dict,
        memory_type: MemoryType,
        confidence: float = 1.0,
        source_type: str | None = None,
        last_confirmed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryFact:
        with self._session_factory() as session:
            fact = MemoryRepository(session).upsert_fact(
                user_id=user_id,
                key=key,
                value_json=value_json,
                memory_type=memory_type,
                confidence=confidence,
                source_type=source_type,
                last_confirmed_at=last_confirmed_at,
                expires_at=expires_at,
            )
            session.commit()
            return fact

    def get_relevant_memory_items(
        self,
        *,
        user_id: int,
        query: str,
        limit: int = 5,
    ) -> list[str]:
        with self._session_factory() as session:
            facts = MemoryRepository(session).list_facts(user_id=user_id, limit=None)
        ranked = [fact for fact in facts if self._score_fact(query=query, fact=fact) > 0]
        ranked.sort(key=lambda fact: self._rank_key(query=query, fact=fact), reverse=True)
        return [self._render_fact(fact) for fact in ranked[:limit]]

    def build_context(
        self,
        *,
        user_id: int,
        text: str,
        current_timezone: str = "America/Los_Angeles",
        now: datetime | None = None,
        candidate_tasks: list[str] | None = None,
        memory_limit: int = 5,
    ) -> ConversationContext:
        memory_items = self.get_relevant_memory_items(user_id=user_id, query=text, limit=memory_limit)
        return self._context_builder.build(
            text,
            current_timezone=current_timezone,
            now=now,
            memory_items=memory_items,
            candidate_tasks=candidate_tasks or [],
        )

    def save_conversation_summary(
        self,
        *,
        user_id: int,
        summary_text: str,
        relevance_window_start: datetime | None = None,
        relevance_window_end: datetime | None = None,
    ) -> ConversationSummary:
        with self._session_factory() as session:
            summary = ConversationSummary(
                user_id=user_id,
                summary_text=summary_text,
                relevance_window_start=relevance_window_start,
                relevance_window_end=relevance_window_end,
            )
            session.add(summary)
            session.commit()
            return summary

    def list_conversation_summaries(self, *, user_id: int, limit: int | None = None) -> list[ConversationSummary]:
        with self._session_factory() as session:
            query = session.query(ConversationSummary).filter(ConversationSummary.user_id == user_id)
            query = query.order_by(ConversationSummary.created_at.desc(), ConversationSummary.id.desc())
            if limit is not None:
                query = query.limit(limit)
            return list(query)

    def replace_active_context(
        self,
        *,
        user_id: int,
        context_type: str,
        payload_json: dict,
        expires_at: datetime | None = None,
    ) -> ActiveContext:
        with self._session_factory() as session:
            context = ContextRepository(session).replace(
                user_id=user_id,
                context_type=context_type,
                payload_json=payload_json,
                expires_at=expires_at,
            )
            session.commit()
            return context

    def get_active_context(self, *, user_id: int, context_type: str) -> ActiveContext | None:
        with self._session_factory() as session:
            return ContextRepository(session).get_by_user_and_type(user_id=user_id, context_type=context_type)

    def clear_active_context(self, *, user_id: int, context_type: str) -> None:
        with self._session_factory() as session:
            context = ContextRepository(session).get_by_user_and_type(user_id=user_id, context_type=context_type)
            if context is None:
                return
            ContextRepository(session).delete(context)
            session.commit()

    def _score_fact(self, *, query: str, fact: MemoryFact) -> int:
        query_text = query.casefold()
        fact_text = self._fact_search_text(fact).casefold()
        score = 0
        if fact.key.casefold() in query_text:
            score += 4
        if fact_text and fact_text in query_text:
            score += 2
        if query_text in fact_text:
            score += 1
        for token in self._search_tokens(fact):
            if len(token) < 2:
                continue
            if token in query_text:
                score += 1
        return score

    def _rank_key(self, *, query: str, fact: MemoryFact) -> tuple[int, int, datetime]:
        type_priority = {
            MemoryType.ALIAS_MAPPING.value: 3,
            MemoryType.TIME_EXPRESSION.value: 2,
            MemoryType.PREFERENCE.value: 1,
            MemoryType.STYLE_PREFERENCE.value: 1,
            MemoryType.DISAMBIGUATION_PATTERN.value: 0,
        }.get(fact.memory_type, 0)
        confirmed_at = fact.last_confirmed_at or datetime.min.replace(tzinfo=timezone.utc)
        if confirmed_at.tzinfo is None:
            confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
        return (self._score_fact(query=query, fact=fact), type_priority, confirmed_at)

    def _render_fact(self, fact: MemoryFact) -> str:
        if fact.memory_type == MemoryType.ALIAS_MAPPING.value:
            return f"alias_mapping: {fact.key} -> {self._value_text(fact, preferred_keys=('canonical_name', 'value', 'target'))}"
        if fact.memory_type == MemoryType.TIME_EXPRESSION.value:
            return f"time_expression: {fact.key} -> {self._value_text(fact, preferred_keys=('normalized', 'value', 'resolved'))}"
        if fact.memory_type == MemoryType.PREFERENCE.value or fact.memory_type == MemoryType.STYLE_PREFERENCE.value:
            return f"{fact.memory_type}: {fact.key} = {self._value_text(fact, preferred_keys=('value', 'preference', 'style'))}"
        return f"{fact.memory_type}: {fact.key} = {json.dumps(fact.value_json, ensure_ascii=False, sort_keys=True)}"

    def _value_text(self, fact: MemoryFact, *, preferred_keys: tuple[str, ...]) -> str:
        for key in preferred_keys:
            value = fact.value_json.get(key)
            if value is not None and value != "":
                return str(value)
        return json.dumps(fact.value_json, ensure_ascii=False, sort_keys=True)

    def _fact_search_text(self, fact: MemoryFact) -> str:
        return " ".join([fact.key, json.dumps(fact.value_json, ensure_ascii=False, sort_keys=True)])

    def _search_tokens(self, fact: MemoryFact) -> set[str]:
        raw_text = self._fact_search_text(fact)
        normalized = raw_text.casefold().replace("->", " ").replace(":", " ").replace(",", " ")
        return {token for token in normalized.split() if token}
