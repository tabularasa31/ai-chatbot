"""Add clarification_count column to chats table.

Tracks per-session blocking clarification budget consumption.
Column defaults to 0 and is incremented only when decide() returns
Decision.clarify (blocking clarify). Never reset within a session.

Revision ID: add_clarification_count_v1
Revises: widen_document_file_type_v1
Create Date: 2026-04-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "add_clarification_count_v1"
down_revision = "widen_document_file_type_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column(
            "clarification_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("chats", "clarification_count")
