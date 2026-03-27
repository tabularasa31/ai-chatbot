"""Add actor/path audit fields for PII events.

Revision ID: fi_pii_access_audit_v1
Revises: fi_pii_hardening_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fi_pii_access_audit_v1"
down_revision = "fi_pii_hardening_v1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False
    return column in cols


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


def downgrade() -> None:
    """Upgrade-only migration for deployed environments."""
    pass
