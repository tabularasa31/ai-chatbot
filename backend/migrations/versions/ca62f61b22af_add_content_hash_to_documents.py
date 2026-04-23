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
    op.create_index("ix_documents_tenant_content_hash", "documents", ["tenant_id", "content_hash"])


def downgrade() -> None:
    op.drop_index("ix_documents_tenant_content_hash", table_name="documents")
    op.drop_column("documents", "content_hash")

