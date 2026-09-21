"""add custom_instructions and preset to bots

Revision ID: f8df9e78d4a5
Revises: visitor_read_cursor_v1
Create Date: 2026-09-21 20:20:56.565878

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8df9e78d4a5'
down_revision = 'visitor_read_cursor_v1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bots", sa.Column("custom_instructions", sa.Text(), nullable=True))
    op.add_column(
        "bots",
        sa.Column("preset", sa.String(64), nullable=True, server_default="support_agent"),
    )


def downgrade() -> None:
    op.drop_column("bots", "preset")
    op.drop_column("bots", "custom_instructions")

