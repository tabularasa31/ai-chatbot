"""add reset password fields to users

Revision ID: add_reset_password
Revises: add_public_id
Create Date: 2026-03-19

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


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
    op.drop_constraint("uq_users_reset_password_token", "users", type_="unique")
    op.drop_column("users", "reset_password_expires_at")
    op.drop_column("users", "reset_password_token")
