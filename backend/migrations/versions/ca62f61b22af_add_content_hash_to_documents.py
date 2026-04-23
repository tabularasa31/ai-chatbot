"""add content_hash to documents

Revision ID: ca62f61b22af
Revises: d8cff32758a0
Create Date: 2026-04-23 22:20:30.104938

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ca62f61b22af'
down_revision = 'd8cff32758a0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_hash", sa.String(64), nullable=True))
    # Partial unique index — covers only direct file uploads (source_id IS NULL).
    # URL-crawled pages are excluded so re-indexing the same page doesn't conflict.
    # Backfill is not possible: original file bytes are not persisted; the service
    # falls back to filename matching for pre-migration rows (content_hash IS NULL).
    op.create_index(
        "ix_documents_tenant_content_hash",
        "documents",
        ["tenant_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("source_id IS NULL AND content_hash IS NOT NULL"),
        sqlite_where=sa.text("source_id IS NULL AND content_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_documents_tenant_content_hash", table_name="documents")
    op.drop_column("documents", "content_hash")

