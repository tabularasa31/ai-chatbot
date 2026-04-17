"""Add quick_answers storage for URL-source fact extraction.

Revision ID: qa_url_answers_v1
Revises: gap_jobs_v1, phase4_user_sessions_active_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "qa_url_answers_v1"
down_revision = ("gap_jobs_v1", "phase4_user_sessions_active_v1")
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return getattr(bind.dialect, "name", "") == "postgresql"


def _has_table(name: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def upgrade() -> None:
    is_postgres = _is_postgres()
    uuid_type = postgresql.UUID(as_uuid=True) if is_postgres else sa.String(length=36)
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    if not _has_table("quick_answers"):
        op.create_table(
            "quick_answers",
            sa.Column(
                "id",
                uuid_type,
                primary_key=True,
                nullable=False,
                server_default=sa.text("gen_random_uuid()") if is_postgres else None,
            ),
            sa.Column("tenant_id", uuid_type, nullable=False),
            sa.Column("source_id", uuid_type, nullable=False),
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column(
                "metadata",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'::json") if is_postgres else None,
            ),
            sa.Column("detected_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_id"], ["url_sources.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("source_id", "key", name="uq_quick_answers_source_key"),
        )
        op.create_index("ix_quick_answers_tenant_id", "quick_answers", ["tenant_id"])
        op.create_index("ix_quick_answers_source_id", "quick_answers", ["source_id"])
        op.create_index("ix_quick_answers_tenant_key", "quick_answers", ["tenant_id", "key"])


def downgrade() -> None:
    """Intentionally empty: upgrade is conditional and must not drop pre-existing tables."""
