"""Phase 4: add cluster_size and source_message_ids to tenant_faq.

Revision ID: phase4_faq_explain_v1
Revises: phase4_log_analysis_state_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "phase4_faq_explain_v1"
down_revision = "phase4_log_analysis_state_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_faq",
        sa.Column("cluster_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_faq",
        sa.Column(
            "source_message_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
