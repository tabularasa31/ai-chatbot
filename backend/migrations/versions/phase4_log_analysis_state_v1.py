"""Phase 4: create log_analysis_state table.

Revision ID: phase4_log_analysis_state_v1
Revises: knowledge_profile_status_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "phase4_log_analysis_state_v1"
down_revision = "knowledge_profile_status_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "log_analysis_state",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_processed_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "messages_since_last_run",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_running",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("last_run_status", sa.Text(), nullable=True),
        sa.Column(
            "last_run_faq_created",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_run_aliases_created",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "analysis_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["clients.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
