"""Add escalation language to tenant profiles."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

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
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
