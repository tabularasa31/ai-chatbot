"""Add durable Gap Analyzer job queue.

Revision ID: gap_jobs_v1
Revises: gap_analyzer_phase1_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "gap_jobs_v1"
down_revision = "gap_analyzer_phase1_v1"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return getattr(bind.dialect, "name", "") == "postgresql"


def upgrade() -> None:
    is_postgres = _is_postgres()
    uuid_type = postgresql.UUID(as_uuid=True) if is_postgres else sa.String(length=36)

    op.create_table(
        "gap_analyzer_jobs",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("tenant_id", uuid_type, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "job_kind",
            sa.Enum(
                "mode_a",
                "mode_b",
                "mode_b_weekly_reclustering",
                name="gapjobkind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "in_progress",
                "retry",
                "completed",
                "failed",
                name="gapjobstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("trigger", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("leased_at", sa.DateTime(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_gap_jobs_status_available", "gap_analyzer_jobs", ["status", "available_at"])
    op.create_index(
        "ix_gap_jobs_tenant_kind_status",
        "gap_analyzer_jobs",
        ["tenant_id", "job_kind", "status"],
    )
    op.create_index("ix_gap_jobs_lease_expires", "gap_analyzer_jobs", ["lease_expires_at"])


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
