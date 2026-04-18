"""Add escalation language to tenant profiles."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "lang_escalation_v1"
down_revision = "qa_url_answers_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_profiles",
        sa.Column("escalation_language", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
