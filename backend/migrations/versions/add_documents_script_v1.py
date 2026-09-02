"""Add documents.script for parse-time writing-system detection.

Stores the document's dominant Unicode script (e.g. "latin", "cyrillic",
"arabic") detected once at parse time. Cross-lingual retrieval used to derive
this from ``documents.language`` through a hand-listed table of ISO codes,
which only covered two script families; the stored value is derived from the
characters themselves and covers every writing system.

Existing rows are backfilled from a prefix of ``parsed_text`` so a KB indexed
before this migration keeps the cheap whole-KB lookup instead of falling back
to query-time chunk sampling.

Revision ID: add_documents_script_v1
Revises: escalation_reply_token_v1
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op
from backend.core.scripts import detect_script_bucket

revision = "add_documents_script_v1"
down_revision = "escalation_reply_token_v1"
branch_labels = None
depends_on = None

_BACKFILL_BATCH_SIZE = 500
_BACKFILL_TEXT_PREFIX = 4096


def _backfill(bind) -> None:
    while True:
        rows = bind.execute(
            sa.text(
                "SELECT id, substr(parsed_text, 1, :prefix) AS sample "
                "FROM documents "
                "WHERE script IS NULL AND parsed_text IS NOT NULL "
                "LIMIT :batch"
            ),
            {"prefix": _BACKFILL_TEXT_PREFIX, "batch": _BACKFILL_BATCH_SIZE},
        ).fetchall()
        if not rows:
            return
        bind.execute(
            sa.text("UPDATE documents SET script = :script WHERE id = :id"),
            [
                {"id": row.id, "script": detect_script_bucket(row.sample)}
                for row in rows
            ],
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("documents")}
    if "script" not in cols:
        op.add_column(
            "documents",
            sa.Column("script", sa.String(length=32), nullable=True),
        )

    indexes = {i["name"] for i in inspector.get_indexes("documents")}
    if "ix_documents_tenant_script" not in indexes:
        op.create_index(
            "ix_documents_tenant_script",
            "documents",
            ["tenant_id", "script"],
        )

    _backfill(bind)


def downgrade() -> None:
    # Documented no-op per project Alembic policy: never drop columns that may
    # hold real data. Re-running upgrade is idempotent via the guards above.
    pass
