"""Create answer_cache_entries — the semantic level of the chat answer cache.

The exact level of the cache lives in Redis; this table holds the question
embeddings the semantic level searches by cosine distance (pgvector), scoped
by tenant, bot, response language and a knowledge-base fingerprint, with a
per-row expiry. No vector index: a scope holds at most the questions asked
within one TTL window, so an exact scan inside the scope index is cheap, and
the table is purged opportunistically on every write.

Row-level security uses the standard tenant-scoped policy (see
backend/core/rls.py); the policy DDL is a frozen snapshot, not an import.

Idempotent: inspects live state before acting.

Revision ID: answer_cache_v1
Revises: add_documents_script_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql

revision = "answer_cache_v1"
down_revision = "add_documents_script_v1"
branch_labels = None
depends_on = None

_TABLE = "answer_cache_entries"
_POLICY = "answer_cache_entries_tenant_isolation"
_CTX = "NULLIF(current_setting('app.tenant_id', true), '')"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    is_postgres = bind.dialect.name == "postgresql"

    if _TABLE not in insp.get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("bot_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("kb_fingerprint", sa.String(length=32), nullable=False),
            sa.Column("response_language", sa.String(length=16), nullable=False),
            sa.Column("question_hash", sa.String(length=64), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        if is_postgres:
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
            op.execute(
                f"ALTER TABLE {_TABLE} ADD COLUMN question_embedding vector(1536) NOT NULL"
            )
        else:
            op.add_column(_TABLE, sa.Column("question_embedding", sa.Text(), nullable=False))
        op.create_index(f"ix_{_TABLE}_tenant_id", _TABLE, ["tenant_id"])
        op.create_index(
            f"ix_{_TABLE}_scope",
            _TABLE,
            ["tenant_id", "bot_id", "kb_fingerprint", "response_language"],
        )
        op.create_index(f"ix_{_TABLE}_expires_at", _TABLE, ["expires_at"])

    if is_postgres:
        op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
        op.execute(
            f"CREATE POLICY {_POLICY} ON {_TABLE} FOR ALL "
            f"USING ({_CTX} IS NULL OR tenant_id = {_CTX}::uuid)"
        )


def downgrade() -> None:
    # Documentation only; downgrades are never run against shared databases.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.drop_table(_TABLE)
