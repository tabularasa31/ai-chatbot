"""KYC widget identity: user_sessions, clients KYC columns, chats.user_context

Revision ID: fi_kyc_v1
Revises: fi032_health
Create Date: 2026-03-21

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fi_kyc_v1"
down_revision = "fi032_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("kyc_secret_key", sa.String(512), nullable=True))
    op.add_column("clients", sa.Column("kyc_secret_key_previous", sa.String(512), nullable=True))
    op.add_column(
        "clients",
        sa.Column("kyc_secret_previous_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column("clients", sa.Column("kyc_secret_key_hint", sa.String(8), nullable=True))

    op.add_column("chats", sa.Column("user_context", sa.JSON(), nullable=True))

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("plan_tier", sa.String(64), nullable=True),
        sa.Column("audience_tag", sa.String(128), nullable=True),
        sa.Column("session_started_at", sa.DateTime(), nullable=False),
        sa.Column("session_ended_at", sa.DateTime(), nullable=True),
        sa.Column("conversation_turns", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_sessions_client_id"), "user_sessions", ["client_id"])
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_client_user", "user_sessions", ["client_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_sessions_client_user", table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_client_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_column("chats", "user_context")
    op.drop_column("clients", "kyc_secret_key_hint")
    op.drop_column("clients", "kyc_secret_previous_expires_at")
    op.drop_column("clients", "kyc_secret_key_previous")
    op.drop_column("clients", "kyc_secret_key")
