"""Widen documents.file_type column to VARCHAR(32).

The column was originally created as VARCHAR(8) (length of the longest
initial enum value 'markdown'). New values 'docx', 'doc', 'plaintext'
require at least 9 characters; VARCHAR(32) gives comfortable room for
future additions. On PostgreSQL, widening VARCHAR is metadata-only and
does not rewrite the table.

Revision ID: widen_document_file_type_v1
Revises: ca62f61b22af
Create Date: 2026-04-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "widen_document_file_type_v1"
down_revision = "ca62f61b22af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "documents",
        "file_type",
        type_=sa.String(32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "documents",
        "file_type",
        type_=sa.String(8),
        existing_nullable=False,
    )
