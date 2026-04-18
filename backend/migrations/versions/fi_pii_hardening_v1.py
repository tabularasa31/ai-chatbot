"""Add PII hardening storage fields and audit table.

Revision ID: fi_pii_hardening_v1
Revises: fi_url_sources_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fi_pii_hardening_v1"
down_revision = "fi_url_sources_v1"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    if op.get_context().as_sql:
        return name in {"messages", "escalation_tickets"}
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _has_column(table: str, column: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False
    return column in cols


def upgrade() -> None:
    if _has_table("messages"):
        if not _has_column("messages", "content_original_encrypted"):
            op.add_column("messages", sa.Column("content_original_encrypted", sa.Text(), nullable=True))
        if not _has_column("messages", "content_redacted"):
            op.add_column("messages", sa.Column("content_redacted", sa.Text(), nullable=True))

    if _has_table("escalation_tickets"):
        if not _has_column("escalation_tickets", "primary_question_original_encrypted"):
            op.add_column(
                "escalation_tickets",
                sa.Column("primary_question_original_encrypted", sa.Text(), nullable=True),
            )
        if not _has_column("escalation_tickets", "primary_question_redacted"):
            op.add_column(
                "escalation_tickets",
                sa.Column("primary_question_redacted", sa.Text(), nullable=True),
            )

    if not _has_table("pii_events"):
        op.create_table(
            "pii_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("direction", sa.String(length=32), nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_pii_events_client_id", "pii_events", ["client_id"])
        op.create_index("ix_pii_events_chat_id", "pii_events", ["chat_id"])
        op.create_index("ix_pii_events_message_id", "pii_events", ["message_id"])
        op.create_index("ix_pii_events_direction", "pii_events", ["direction"])


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
