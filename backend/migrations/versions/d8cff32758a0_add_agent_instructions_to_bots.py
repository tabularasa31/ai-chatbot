"""add agent_instructions to bots

Revision ID: d8cff32758a0
Revises: chat_bot_scope_v1
Create Date: 2026-04-23 13:32:55.268484

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'd8cff32758a0'
down_revision = 'chat_bot_scope_v1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bots", sa.Column("agent_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("bots", "agent_instructions")
