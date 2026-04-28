"""Add documents.language column for parse-time language detection.

Stores the document's primary language as an ISO 639-1 code (e.g. "en", "ru")
detected once at parse time. This replaces the query-time chunk sampling in
``detect_tenant_kb_script`` and lets us correctly identify mixed-language KBs.

Revision ID: add_documents_language_v1
Revises: rename_tp_modules_to_topics_v1
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa


revision = "add_documents_language_v1"
down_revision = "rename_tp_modules_to_topics_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("documents")}
    if "language" not in cols:
        op.add_column(
            "documents",
            sa.Column("language", sa.String(length=8), nullable=True),
        )

    indexes = {i["name"] for i in inspector.get_indexes("documents")}
    if "ix_documents_tenant_language" not in indexes:
        op.create_index(
            "ix_documents_tenant_language",
            "documents",
            ["tenant_id", "language"],
        )


def downgrade() -> None:
    # Documented no-op per project Alembic policy: never drop columns that may
    # hold real data. Re-running upgrade is idempotent via the IF-NOT-EXISTS
    # guards above.
    pass
