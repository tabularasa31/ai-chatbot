"""Restore original message/ticket text into the primary content columns.

Redaction moves from storage to the egress boundaries: ``messages.content``
and ``escalation_tickets.primary_question`` must hold the ORIGINAL wording
again. Until now they held the masked copy, with the original available only
in the ``*_original_encrypted`` columns.

This revision is the backfill half of that move and MUST run before
``pii_drop_redacted_columns_v1`` drops those encrypted columns — dropping them
first would destroy the only copy of the originals.

Rows without an encrypted original (written before the column existed, or
while ``ENCRYPTION_KEY`` was unset) are left exactly as they are: their masked
text is the only text there is, and blanking it would lose the row's content
entirely. The same applies to rows whose ciphertext fails to decrypt under the
current key.

Revision ID: pii_restore_originals_v1
Revises: guard_events_purge_index_v1
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "pii_restore_originals_v1"
down_revision = "guard_events_purge_index_v1"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_BATCH = 500


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


def _restore(bind, table: str, target: str, encrypted: str) -> tuple[int, int]:
    """Copy decrypted ``encrypted`` into ``target``. Returns (restored, skipped).

    Takes the connection explicitly so the backfill can be exercised directly
    in tests without an alembic migration context.
    """
    from backend.core.crypto import decrypt_value

    select_sql = (
        f"SELECT id, {encrypted} FROM {table} "
        f"WHERE {encrypted} IS NOT NULL AND {encrypted} <> ''"
    )
    update_sql = f"UPDATE {table} SET {target} = :plaintext WHERE id = :row_id"
    rows = bind.execute(sa.text(select_sql)).fetchall()

    restored = 0
    skipped = 0
    pending: list[dict[str, object]] = []
    for row_id, ciphertext in rows:
        try:
            plaintext = decrypt_value(ciphertext)
        except Exception:
            # Unset/rotated ENCRYPTION_KEY or corrupt ciphertext: leave the row
            # untouched rather than blanking the only readable text it has.
            skipped += 1
            continue
        pending.append({"row_id": row_id, "plaintext": plaintext})
        if len(pending) >= _BATCH:
            bind.execute(sa.text(update_sql), pending)
            restored += len(pending)
            pending = []
    if pending:
        bind.execute(sa.text(update_sql), pending)
        restored += len(pending)
    return restored, skipped


def upgrade() -> None:
    if op.get_context().as_sql:
        # Offline (--sql) mode cannot decrypt row-by-row; the backfill has to
        # run against a live connection.
        raise RuntimeError(
            "pii_restore_originals_v1 needs a live database connection "
            "(it decrypts each row); run it without --sql."
        )

    targets = (
        ("messages", "content", "content_original_encrypted"),
        (
            "escalation_tickets",
            "primary_question",
            "primary_question_original_encrypted",
        ),
    )
    for table, target, encrypted in targets:
        if not _has_column(table, encrypted):
            continue
        restored, skipped = _restore(op.get_bind(), table, target, encrypted)
        logger.info(
            "pii_restore_originals_v1: %s -> restored=%s skipped=%s",
            table,
            restored,
            skipped,
        )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # There is nothing to undo here anyway — the encrypted originals this
    # revision copies from are left in place, so re-masking would only destroy
    # the text it just restored.
    raise NotImplementedError("downgrade is not supported for this migration")
