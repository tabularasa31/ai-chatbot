"""add_tenant_profiles_and_faq

Revision ID: add_tenant_profiles_and_faq
Revises: eval_qa_mvp_v1
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "add_tenant_profiles_and_faq"
down_revision = "eval_qa_mvp_v1"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = getattr(bind.dialect, "name", "") == "postgresql"
    json_default = sa.text("'[]'::jsonb") if is_postgres else sa.text("'[]'")

    # Create tenant_profiles
    if not _has_table("tenant_profiles"):
        op.create_table(
            "tenant_profiles",
            sa.Column("tenant_id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column("product_name", sa.Text(), nullable=True),
            sa.Column("modules", sa.JSON(), nullable=False, server_default=json_default),
            sa.Column("glossary", sa.JSON(), nullable=False, server_default=json_default),
            sa.Column("aliases", sa.JSON(), nullable=False, server_default=json_default),
            sa.Column("support_email", sa.Text(), nullable=True),
            sa.Column("support_urls", sa.JSON(), nullable=False, server_default=json_default),
            sa.Column("escalation_policy", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_tenant_profiles_updated_at", "tenant_profiles", ["updated_at"], unique=False)

    # Create tenant_faq
    if not _has_table("tenant_faq"):
        if is_postgres:
            op.execute(
                "CREATE EXTENSION IF NOT EXISTS vector"
            )
            op.execute(
                """
                CREATE TABLE tenant_faq (
                    id UUID PRIMARY KEY NOT NULL,
                    tenant_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    question_embedding vector(1536),
                    confidence DOUBLE PRECISION,
                    source TEXT,
                    approved BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            op.execute("CREATE INDEX ix_tenant_faq_tenant_id ON tenant_faq (tenant_id)")
            # Speed up cosine-nearest search for FAQ dedupe.
            op.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_tenant_faq_question_embedding_ivfflat
                ON tenant_faq
                USING ivfflat (question_embedding vector_cosine_ops)
                """
            )
        else:
            op.create_table(
                "tenant_faq",
                sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
                sa.Column("tenant_id", sa.UUID(), nullable=False),
                sa.Column("question", sa.Text(), nullable=False),
                sa.Column("answer", sa.Text(), nullable=False),
                sa.Column("question_embedding", sa.Text(), nullable=True),
                sa.Column("confidence", sa.Float(), nullable=True),
                sa.Column("source", sa.Text(), nullable=True),
                sa.Column(
                    "approved", sa.Boolean(), nullable=False, server_default=sa.text("false")
                ),
                sa.Column(
                    "created_at",
                    sa.DateTime(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
                sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
            )
            op.create_index(
                "ix_tenant_faq_tenant_id",
                "tenant_faq",
                ["tenant_id"],
                unique=False,
            )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
