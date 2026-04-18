"""FI-ESC: escalation_tickets table and chat escalation state columns.

Revision ID: fi_esc_v1
Revises: fi_disc_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fi_esc_v1"
down_revision = "fi_disc_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escalation_tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("ticket_number", sa.String(32), nullable=False),
        sa.Column("primary_question", sa.Text(), nullable=False),
        sa.Column("conversation_summary", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("best_similarity_score", sa.Float(), nullable=True),
        sa.Column("retrieved_chunks_preview", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.String(255), nullable=True),
        sa.Column("user_email", sa.String(255), nullable=True),
        sa.Column("user_name", sa.String(255), nullable=True),
        sa.Column("plan_tier", sa.String(64), nullable=True),
        sa.Column("user_note", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(32), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("resolution_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("chat_id", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "ticket_number", name="uq_escalation_client_ticket_number"),
    )
    op.create_index(op.f("ix_escalation_tickets_client_id"), "escalation_tickets", ["client_id"])
    op.create_index(op.f("ix_escalation_tickets_ticket_number"), "escalation_tickets", ["ticket_number"])
    op.create_index(op.f("ix_escalation_tickets_trigger"), "escalation_tickets", ["trigger"])
    op.create_index(op.f("ix_escalation_tickets_status"), "escalation_tickets", ["status"])
    op.create_index(op.f("ix_escalation_tickets_created_at"), "escalation_tickets", ["created_at"])
    op.create_index(op.f("ix_escalation_tickets_user_id"), "escalation_tickets", ["user_id"])
    op.create_index(op.f("ix_escalation_tickets_chat_id"), "escalation_tickets", ["chat_id"])
    op.create_index(op.f("ix_escalation_tickets_session_id"), "escalation_tickets", ["session_id"])

    op.add_column(
        "chats",
        sa.Column("escalation_awaiting_ticket_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "chats",
        sa.Column("escalation_followup_pending", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("chats", sa.Column("ended_at", sa.DateTime(), nullable=True))
    op.create_foreign_key(
        "fk_chats_escalation_awaiting_ticket_id",
        "chats",
        "escalation_tickets",
        ["escalation_awaiting_ticket_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_chats_escalation_awaiting_ticket_id"),
        "chats",
        ["escalation_awaiting_ticket_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chats_escalation_awaiting_ticket_id"), table_name="chats")
    op.drop_constraint("fk_chats_escalation_awaiting_ticket_id", "chats", type_="foreignkey")
    op.drop_column("chats", "ended_at")
    op.drop_column("chats", "escalation_followup_pending")
    op.drop_column("chats", "escalation_awaiting_ticket_id")

    op.drop_index(op.f("ix_escalation_tickets_session_id"), table_name="escalation_tickets")
    op.drop_index(op.f("ix_escalation_tickets_chat_id"), table_name="escalation_tickets")
    op.drop_index(op.f("ix_escalation_tickets_user_id"), table_name="escalation_tickets")
    op.drop_index(op.f("ix_escalation_tickets_created_at"), table_name="escalation_tickets")
    op.drop_index(op.f("ix_escalation_tickets_status"), table_name="escalation_tickets")
    op.drop_index(op.f("ix_escalation_tickets_trigger"), table_name="escalation_tickets")
    op.drop_index(op.f("ix_escalation_tickets_ticket_number"), table_name="escalation_tickets")
    op.drop_index(op.f("ix_escalation_tickets_client_id"), table_name="escalation_tickets")
    op.drop_table("escalation_tickets")
