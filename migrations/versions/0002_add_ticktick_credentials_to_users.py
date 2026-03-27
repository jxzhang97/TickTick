"""add ticktick credentials to users

Revision ID: 0002_add_ticktick_credentials_to_users
Revises: 0001_initial_schema
Create Date: 2026-03-27 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_add_ticktick_credentials_to_users"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ticktick_access_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("ticktick_refresh_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("ticktick_token_type", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("ticktick_token_scope", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("ticktick_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("ticktick_connected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "ticktick_connected_at")
    op.drop_column("users", "ticktick_token_expires_at")
    op.drop_column("users", "ticktick_token_scope")
    op.drop_column("users", "ticktick_token_type")
    op.drop_column("users", "ticktick_refresh_token")
    op.drop_column("users", "ticktick_access_token")
