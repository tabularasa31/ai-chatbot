"""add email verification fields to users

Revision ID: add_email_verification
Revises: add_client_id
Create Date: 2026-03-18

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "add_email_verification"
down_revision = "add_client_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("verification_token", sa.String(128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("verification_expires_at", sa.DateTime(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_verification_token",
        "users",
        ["verification_token"],
    )


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
