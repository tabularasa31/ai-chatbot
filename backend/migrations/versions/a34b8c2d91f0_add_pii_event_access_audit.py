"""Add actor/path audit fields for PII events.

Revision ID: a34b8c2d91f0
Revises: fi_pii_hardening_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a34b8c2d91f0"
down_revision = "fi_pii_hardening_v1"
branch_labels = None
depends_on = None


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


def _has_index(table: str, index_name: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        indexes = {idx["name"] for idx in insp.get_indexes(table)}
    except Exception:
        return False
    return index_name in indexes


def upgrade() -> None:
    if not _has_column("pii_events", "actor_user_id"):
        op.add_column("pii_events", sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_pii_events_actor_user_id_users",
            "pii_events",
            "users",
            ["actor_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_pii_events_actor_user_id", "pii_events", ["actor_user_id"])
    if not _has_column("pii_events", "action_path"):
        op.add_column("pii_events", sa.Column("action_path", sa.String(length=255), nullable=True))
    if not _has_index("pii_events", "ix_pii_events_action_path"):
        op.create_index("ix_pii_events_action_path", "pii_events", ["action_path"])


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
