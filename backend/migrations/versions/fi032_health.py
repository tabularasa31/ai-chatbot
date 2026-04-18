"""add document health_status JSON column

Revision ID: fi032_health
Revises: dd643d1a544a
Create Date: 2026-03-21

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fi032_health"
down_revision = "dd643d1a544a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("health_status", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
