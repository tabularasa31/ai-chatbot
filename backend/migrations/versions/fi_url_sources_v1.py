"""Add URL source entities and document linkage for documentation crawling.

Revision ID: fi_url_sources_v1
Revises: repair_is_admin_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fi_url_sources_v1"
down_revision = "repair_is_admin_v1"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    if op.get_context().as_sql:
        return name == "documents"
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _has_column(table: str, column: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False
    return column in cols


def upgrade() -> None:
    if not _has_table("url_sources"):
        op.create_table(
            "url_sources",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("normalized_domain", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("crawl_schedule", sa.String(length=32), nullable=False, server_default="weekly"),
            sa.Column("exclusion_patterns", sa.JSON(), nullable=True),
            sa.Column("pages_found", sa.Integer(), nullable=True),
            sa.Column("pages_indexed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("chunks_created", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_crawled_at", sa.DateTime(), nullable=True),
            sa.Column("next_crawl_at", sa.DateTime(), nullable=True),
            sa.Column("last_refresh_requested_at", sa.DateTime(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("warning_message", sa.Text(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_url_sources_client_id", "url_sources", ["client_id"])
        op.create_index("ix_url_sources_normalized_domain", "url_sources", ["normalized_domain"])
        op.create_index("ix_url_sources_status", "url_sources", ["status"])

    if not _has_table("url_source_runs"):
        op.create_table(
            "url_source_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("pages_found", sa.Integer(), nullable=True),
            sa.Column("pages_indexed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_urls", sa.JSON(), nullable=False),
            sa.Column("duration_seconds", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["source_id"], ["url_sources.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_url_source_runs_source_id", "url_source_runs", ["source_id"])

    if not _has_column("documents", "source_id"):
        op.add_column("documents", sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_documents_source_id_url_sources",
            "documents",
            "url_sources",
            ["source_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_documents_source_id", "documents", ["source_id"])

    if not _has_column("documents", "source_url"):
        op.add_column("documents", sa.Column("source_url", sa.Text(), nullable=True))

    # SQLite stores enum values as VARCHAR, so no ALTER TYPE is needed there.
    # PostgreSQL in this repo uses non-native enums on Document.file_type.


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
