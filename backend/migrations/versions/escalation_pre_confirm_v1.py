"""Add escalation_pre_confirm_pending and escalation_pre_confirm_context to chats.

Revision ID: escalation_pre_confirm_v1
Revises: tenant_llm_alert_v1
Create Date: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "escalation_pre_confirm_v1"
down_revision = "tenant_llm_alert_v1"
branch_labels = None
depends_on = None


def _has_column(insp: sa_inspect, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _has_column(insp, "chats", "escalation_pre_confirm_pending"):
        op.add_column(
            "chats",
            sa.Column(
                "escalation_pre_confirm_pending",
                sa.Boolean(),
                server_default="false",
                nullable=False,
            ),
        )
    if not _has_column(insp, "chats", "escalation_pre_confirm_context"):
        op.add_column(
            "chats",
            sa.Column("escalation_pre_confirm_context", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    raise NotImplementedError("downgrade not supported — remove columns manually if needed")
