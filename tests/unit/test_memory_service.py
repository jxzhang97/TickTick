from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.base import Base
from ticktick_telegram_assistant.db.models.active_context import ActiveContext
from ticktick_telegram_assistant.db.models.conversation_summary import ConversationSummary
from ticktick_telegram_assistant.db.models.memory_fact import MemoryFact
from ticktick_telegram_assistant.domain.enums import MemoryType
from ticktick_telegram_assistant.repositories.memory import MemoryRepository
from ticktick_telegram_assistant.services.memory_service import MemoryService


def make_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def test_memory_repository_upserts_fact_in_place() -> None:
    session_factory = make_session_factory()

    with session_factory() as session:
        repo = MemoryRepository(session)
        first = repo.upsert_fact(
            user_id=99,
            key="tone_style",
            value_json={"value": "简短"},
            memory_type=MemoryType.PREFERENCE,
        )
        session.commit()

        second = repo.upsert_fact(
            user_id=99,
            key="tone_style",
            value_json={"value": "详细"},
            memory_type=MemoryType.PREFERENCE,
        )
        session.commit()

        assert first.id == second.id
        rows = session.query(MemoryFact).all()
        assert len(rows) == 1
        assert rows[0].memory_type == MemoryType.PREFERENCE.value
        assert rows[0].value_json == {"value": "详细"}


def test_memory_service_builds_context_with_relevant_memory_items() -> None:
    session_factory = make_session_factory()
    service = MemoryService(session_factory=session_factory)

    service.save_conversation_summary(
        user_id=99,
        summary_text="用户偏好简短回复。",
    )
    service.save_conversation_summary(
        user_id=99,
        summary_text="用户提到明天下午要提醒。",
    )
    with session_factory() as session:
        repo = MemoryRepository(session)
        repo.upsert_fact(
            user_id=99,
            key="老王",
            value_json={"canonical_name": "王老师"},
            memory_type=MemoryType.ALIAS_MAPPING,
        )
        repo.upsert_fact(
            user_id=99,
            key="明天下午",
            value_json={"normalized": "2026-03-28T15:00:00-07:00"},
            memory_type=MemoryType.TIME_EXPRESSION,
        )
        repo.upsert_fact(
            user_id=99,
            key="tone_style",
            value_json={"value": "简短"},
            memory_type=MemoryType.PREFERENCE,
        )
        session.commit()

    context = service.build_context(
        user_id=99,
        text="老王，明天下午提醒我一下",
        current_timezone="America/Los_Angeles",
        now=datetime.fromisoformat("2026-03-27T09:00:00-07:00"),
        candidate_tasks=["task-1: 给导师发邮件"],
    )

    assert context.user_text == "老王，明天下午提醒我一下"
    assert context.current_timezone == "America/Los_Angeles"
    assert context.current_local_time == "2026-03-27T09:00:00-07:00"
    assert context.candidate_tasks == ["task-1: 给导师发邮件"]
    assert context.recent_conversation_summaries == [
        "用户提到明天下午要提醒。",
        "用户偏好简短回复。",
    ]
    assert context.memory_items == [
        "conversation_summary: 用户提到明天下午要提醒。",
        "conversation_summary: 用户偏好简短回复。",
        "alias_mapping: 老王 -> 王老师",
        "time_expression: 明天下午 -> 2026-03-28T15:00:00-07:00",
    ]


def test_memory_service_saves_summary_and_replaces_active_context() -> None:
    session_factory = make_session_factory()
    service = MemoryService(session_factory=session_factory)

    summary = service.save_conversation_summary(
        user_id=99,
        summary_text="用户说明下周要交报告，并偏好简短回复。",
        relevance_window_start=datetime(2026, 3, 20, tzinfo=timezone.utc),
        relevance_window_end=datetime(2026, 3, 27, tzinfo=timezone.utc),
    )
    first_context = service.replace_active_context(
        user_id=99,
        context_type="active_task",
        payload_json={"task_id": "task-1", "title": "给导师发邮件"},
    )
    second_context = service.replace_active_context(
        user_id=99,
        context_type="active_task",
        payload_json={"task_id": "task-2", "title": "整理周报"},
    )

    assert summary.summary_text.startswith("用户说明")
    assert first_context.payload_json["task_id"] == "task-1"
    assert second_context.payload_json["task_id"] == "task-2"

    with session_factory() as session:
        summaries = session.query(ConversationSummary).all()
        contexts = session.query(ActiveContext).all()

    assert len(summaries) == 1
    assert summaries[0].summary_text == "用户说明下周要交报告，并偏好简短回复。"
    assert len(contexts) == 1
    assert contexts[0].context_type == "active_task"
    assert contexts[0].payload_json == {"task_id": "task-2", "title": "整理周报"}

    active_context = service.get_active_context(user_id=99, context_type="active_task")
    assert active_context is not None
    assert active_context.payload_json["task_id"] == "task-2"

    service.clear_active_context(user_id=99, context_type="active_task")
    assert service.get_active_context(user_id=99, context_type="active_task") is None
