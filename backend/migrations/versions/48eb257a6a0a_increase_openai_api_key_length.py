"""increase openai_api_key length

Revision ID: 48eb257a6a0a
Revises: c43e952ca145
Create Date: 2026-03-18 14:47:41.978681

"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = '48eb257a6a0a'
down_revision = 'c43e952ca145'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE clients ALTER COLUMN openai_api_key TYPE VARCHAR(500)"
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
