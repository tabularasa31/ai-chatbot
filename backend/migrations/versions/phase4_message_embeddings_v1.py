"""Phase 4: create message_embeddings table for chat-log analysis.

Revision ID: phase4_message_embeddings_v1
Revises: phase4_faq_explain_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "phase4_message_embeddings_v1"
down_revision = "phase4_faq_explain_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_embeddings",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["clients.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )
    # Add vector column via raw SQL (pgvector type not available in SA column DSL)
    op.execute("ALTER TABLE message_embeddings ADD COLUMN embedding vector(1536)")
    op.create_index(
        "ix_message_embeddings_tenant_last_used",
        "message_embeddings",
        ["tenant_id", "last_used_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_message_embeddings_tenant_last_used", table_name="message_embeddings")
    op.drop_table("message_embeddings")
