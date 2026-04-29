"""Add link safety config to bots.

Revision ID: link_safety_bot_config_v1
Revises: tenant_api_keys_v1
Create Date: 2026-04-28 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "link_safety_bot_config_v1"
down_revision = "tenant_api_keys_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column(
            "link_safety_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("bots", sa.Column("allowed_domains", sa.JSON(), nullable=True))
    op.alter_column("bots", "link_safety_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("bots", "allowed_domains")
    op.drop_column("bots", "link_safety_enabled")
