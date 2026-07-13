"""Add guard_events table for guard verdict logging (FP/FN analysis).

Revision ID: guard_events_v1
Revises: rls_tenant_isolation_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "guard_events_v1"
down_revision = "rls_tenant_isolation_v1"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        return table in set(insp.get_table_names())
    except Exception:
        return False


def upgrade() -> None:
    if _has_table("guard_events"):
        return
    op.create_table(
        "guard_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=True),
        sa.Column("label", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], ondelete="SET NULL"
        ),
    )
    # Keep indexes lean on this write-heavy (2 rows/turn) table: only what
    # FP/FN analysis and the review tool actually filter on. A composite
    # (tenant_id, created_at) covers the core "a tenant's events over a time
    # window" query (and tenant-only scans via its prefix); reason slices by
    # category; label serves the review tool's sparse WHERE label IS NOT NULL.
    # kind/blocked are low-cardinality and chat_id lookups are rare — no index.
    op.create_index(
        "ix_guard_events_tenant_created", "guard_events", ["tenant_id", "created_at"]
    )
    op.create_index("ix_guard_events_reason", "guard_events", ["reason"])
    op.create_index("ix_guard_events_label", "guard_events", ["label"])


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so an accidental `alembic downgrade` errors
    # out instead of silently dropping a telemetry table.
    raise NotImplementedError("downgrade is not supported for this migration")
