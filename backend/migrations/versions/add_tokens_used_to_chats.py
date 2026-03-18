"""add tokens_used to chats

Revision ID: a1b2c3d4e5f6
Revises: add_email_verification
Create Date: 2026-03-18 18:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "add_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("chats", "tokens_used")
