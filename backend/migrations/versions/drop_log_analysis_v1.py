"""Remove the background chat-log analysis job's data.

* Deletes the never-approved FAQ suggestions the job generated
  (``tenant_faq`` rows with ``source = 'logs'`` and ``approved = false``).
  Approved rows are left in place.
* Drops ``log_analysis_state``; its RLS policy goes with the table.

Revision ID: drop_log_analysis_v1
Revises: unanswered_queue_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "drop_log_analysis_v1"
down_revision = "unanswered_queue_v1"
branch_labels = None
depends_on = None

_STATE_TABLE = "log_analysis_state"


def upgrade() -> None:
    op.execute("DELETE FROM tenant_faq WHERE source = 'logs' AND approved = false")
    op.execute(f"DROP TABLE IF EXISTS {_STATE_TABLE}")


def downgrade() -> None:
    """Documentation only; never executed against a shared or production database.

    Recreates the empty table structure. The deleted FAQ suggestions and the
    per-tenant watermarks are not restorable. RLS is not re-applied.
    """
    op.create_table(
        _STATE_TABLE,
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_processed_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("messages_since_last_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_running", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_run_status", sa.Text(), nullable=True),
        sa.Column("last_run_faq_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_aliases_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analysis_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
