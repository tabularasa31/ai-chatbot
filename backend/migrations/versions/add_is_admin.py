"""add is_admin to users

Revision ID: add_is_admin
Revises: 48eb257a6a0a
Create Date: 2026-03-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "add_is_admin"
down_revision = "53879a65961c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
