"""Drop the duplicated redacted/encrypted PII storage columns.

Second half of the storage -> egress move. ``pii_restore_originals_v1`` has
already copied every decryptable original back into ``messages.content`` /
``escalation_tickets.primary_question``, so the masked duplicate and the
encrypted original are now dead weight and are dropped here.

Also purges ``pii_events`` rows whose ``direction`` no longer exists
(``message_storage`` recorded redaction at write time; ``original_view`` /
``original_delete`` audited the removed "view / delete originals" flows).
The column is a plain VARCHAR, so leaving the values in place would make the
ORM raise ``LookupError`` when the privacy log is read.

Revision ID: pii_drop_redacted_columns_v1
Revises: pii_restore_originals_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pii_drop_redacted_columns_v1"
down_revision = "pii_restore_originals_v1"
branch_labels = None
depends_on = None

_DROPPED_COLUMNS = (
    ("messages", "content_original_encrypted"),
    ("messages", "content_redacted"),
    ("escalation_tickets", "primary_question_original_encrypted"),
    ("escalation_tickets", "primary_question_redacted"),
)

_REMOVED_DIRECTIONS = ("message_storage", "original_view", "original_delete")


def _has_table(name: str) -> bool:
    if op.get_context().as_sql:
        return True
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def _has_column(table: str, column: str) -> bool:
    if op.get_context().as_sql:
        return True
    bind = op.get_bind()
    try:
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    except Exception:
        return False
    return column in cols


def upgrade() -> None:
    for table, column in _DROPPED_COLUMNS:
        if _has_table(table) and _has_column(table, column):
            op.drop_column(table, column)

    if _has_table("pii_events"):
        op.execute(
            sa.text(
                "DELETE FROM pii_events WHERE direction IN "
                "('message_storage', 'original_view', 'original_delete')"
            )
        )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Documented for completeness — re-adding the columns would bring back empty
    # ones, and the purged audit rows are gone for good.
    raise NotImplementedError("downgrade is not supported for this migration")
