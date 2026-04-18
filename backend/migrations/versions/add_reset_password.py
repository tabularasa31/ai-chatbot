"""add reset password fields to users

Revision ID: add_reset_password
Revises: add_public_id
Create Date: 2026-03-19

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "add_reset_password"
down_revision = "add_public_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("reset_password_token", sa.String(128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("reset_password_expires_at", sa.DateTime(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_reset_password_token",
        "users",
        ["reset_password_token"],
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
