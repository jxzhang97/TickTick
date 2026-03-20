"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-03-20 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("default_language", sa.String(length=32), nullable=False),
        sa.Column("tone_style", sa.String(length=64), nullable=True),
        sa.Column("primary_timezone", sa.String(length=64), nullable=False),
        sa.Column("current_timezone", sa.String(length=64), nullable=False),
        sa.Column("timezone_source", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)

    op.create_table(
        "memory_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memory_facts_user_id", "memory_facts", ["user_id"], unique=False)
    op.create_index("ix_memory_facts_memory_type", "memory_facts", ["memory_type"], unique=False)
    op.create_index("ix_memory_facts_key", "memory_facts", ["key"], unique=False)

    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("relevance_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("relevance_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversation_summaries_user_id", "conversation_summaries", ["user_id"], unique=False)

    op.create_table(
        "active_contexts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("context_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_active_contexts_user_id", "active_contexts", ["user_id"], unique=False)
    op.create_index("ix_active_contexts_context_type", "active_contexts", ["context_type"], unique=False)

    op.create_table(
        "task_shadows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ticktick_task_id", sa.String(length=128), nullable=False),
        sa.Column("semantic_type", sa.String(length=64), nullable=False),
        sa.Column("normalized_title", sa.String(length=255), nullable=True),
        sa.Column("list_name", sa.String(length=255), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_nl_time", sa.String(length=255), nullable=True),
        sa.Column("timezone_mode", sa.String(length=32), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_task_shadows_user_id", "task_shadows", ["user_id"], unique=False)
    op.create_index("ix_task_shadows_ticktick_task_id", "task_shadows", ["ticktick_task_id"], unique=True)
    op.create_index("ix_task_shadows_semantic_type", "task_shadows", ["semantic_type"], unique=False)

    op.create_table(
        "reminder_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ticktick_task_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("snoozed_from_event_id", sa.Integer(), sa.ForeignKey("reminder_events.id"), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reminder_events_user_id", "reminder_events", ["user_id"], unique=False)
    op.create_index("ix_reminder_events_ticktick_task_id", "reminder_events", ["ticktick_task_id"], unique=False)
    op.create_index("ix_reminder_events_event_type", "reminder_events", ["event_type"], unique=False)
    op.create_index("ix_reminder_events_scheduled_at", "reminder_events", ["scheduled_at"], unique=False)
    op.create_index("ix_reminder_events_status", "reminder_events", ["status"], unique=False)
    op.create_index("ix_reminder_events_dedupe_key", "reminder_events", ["dedupe_key"], unique=True)

    op.create_table(
        "action_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_message_id", sa.Text(), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("parsed_plan_json", sa.JSON(), nullable=True),
        sa.Column("execution_result_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_action_logs_user_id", "action_logs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_action_logs_user_id", table_name="action_logs")
    op.drop_table("action_logs")

    op.drop_index("ix_reminder_events_dedupe_key", table_name="reminder_events")
    op.drop_index("ix_reminder_events_status", table_name="reminder_events")
    op.drop_index("ix_reminder_events_scheduled_at", table_name="reminder_events")
    op.drop_index("ix_reminder_events_event_type", table_name="reminder_events")
    op.drop_index("ix_reminder_events_ticktick_task_id", table_name="reminder_events")
    op.drop_index("ix_reminder_events_user_id", table_name="reminder_events")
    op.drop_table("reminder_events")

    op.drop_index("ix_task_shadows_semantic_type", table_name="task_shadows")
    op.drop_index("ix_task_shadows_ticktick_task_id", table_name="task_shadows")
    op.drop_index("ix_task_shadows_user_id", table_name="task_shadows")
    op.drop_table("task_shadows")

    op.drop_index("ix_active_contexts_context_type", table_name="active_contexts")
    op.drop_index("ix_active_contexts_user_id", table_name="active_contexts")
    op.drop_table("active_contexts")

    op.drop_index("ix_conversation_summaries_user_id", table_name="conversation_summaries")
    op.drop_table("conversation_summaries")

    op.drop_index("ix_memory_facts_key", table_name="memory_facts")
    op.drop_index("ix_memory_facts_memory_type", table_name="memory_facts")
    op.drop_index("ix_memory_facts_user_id", table_name="memory_facts")
    op.drop_table("memory_facts")

    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")
