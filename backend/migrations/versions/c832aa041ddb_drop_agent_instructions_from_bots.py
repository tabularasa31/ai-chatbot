"""drop agent_instructions from bots

Revision ID: c832aa041ddb
Revises: f8df9e78d4a5
Create Date: 2026-09-21 22:11:04.186283

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c832aa041ddb'
down_revision = 'turn_outcome_v1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("bots", "agent_instructions")


def downgrade() -> None:
    # Documentation only: data is not restorable, this recreates an empty column.
    op.add_column("bots", sa.Column("agent_instructions", sa.Text(), nullable=True))

