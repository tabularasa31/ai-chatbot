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


def _require_usable_key() -> None:
    """Abort unless the configured key can be built at all.

    ``_restore`` skips any row it cannot decrypt, which is right for isolated
    corrupt ciphertext but indistinguishable from an absent key — that case
    would skip every row, report success, and let
    ``pii_drop_redacted_columns_v1`` drop the only copy of the originals in the
    same upgrade.
    """
    from backend.core.crypto import get_fernet

    try:
        get_fernet()
    except Exception as exc:
        raise RuntimeError(
            "pii_restore_originals_v1 cannot decrypt stored originals: "
            f"{exc}. Set ENCRYPTION_KEY to the key those rows were written with "
            "and re-run; the next revision drops the encrypted columns, so "
            "continuing would destroy them."
        ) from exc


def _guard_nothing_restored(table: str, restored: int, skipped: int) -> None:
    """Abort when a usable key still decrypted nothing at all.

    Rows existed, the key built fine, and not one of them came back: the key is
    the wrong one (rotated since those rows were written). Stop rather than let
    the next revision drop what this one failed to recover.
    """
    if restored == 0 and skipped > 0:
        raise RuntimeError(
            f"pii_restore_originals_v1: all {skipped} encrypted originals in "
            f"{table} failed to decrypt. ENCRYPTION_KEY looks rotated; aborting "
            "before pii_drop_redacted_columns_v1 destroys them."
        )


def _restore(bind, table: str, target: str, encrypted: str) -> tuple[int, int]:
    """Copy decrypted ``encrypted`` into ``target``. Returns (restored, skipped).

    Takes the connection explicitly so the backfill can be exercised directly
    in tests without an alembic migration context.
    """
    from backend.core.crypto import decrypt_value

    select_stmt = sa.text(
        f"SELECT id, {encrypted} FROM {table} "
        f"WHERE {encrypted} IS NOT NULL AND {encrypted} <> ''"
    ).execution_options(stream_results=True, max_row_buffer=_BATCH)
    update_stmt = sa.text(f"UPDATE {table} SET {target} = :plaintext WHERE id = :row_id")

    # Streamed in pages rather than fetched whole: this runs in the deploy's
    # release step, and materialising an entire ``messages`` table there is how
    # the upgrade gets OOM-killed half-applied. On backends without server-side
    # cursors the option is ignored and ``fetchmany`` still pages correctly.
    result = bind.execute(select_stmt)
    restored = 0
    skipped = 0
    while True:
        rows = result.fetchmany(_BATCH)
        if not rows:
            break
        pending: list[dict[str, object]] = []
        for row_id, ciphertext in rows:
            try:
                plaintext = decrypt_value(ciphertext)
            except Exception:
                # A single corrupt ciphertext: leave the row untouched rather
                # than blanking the only readable text it has. A *systemic*
                # decryption failure is caught by ``upgrade`` below, which
                # refuses to hand over to the column-dropping revision.
                skipped += 1
                continue
            pending.append({"row_id": row_id, "plaintext": plaintext})
        if pending:
            bind.execute(update_stmt, pending)
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

    _require_usable_key()

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
        _guard_nothing_restored(table, restored, skipped)


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # There is nothing to undo here anyway — the encrypted originals this
    # revision copies from are left in place, so re-masking would only destroy
    # the text it just restored.
    raise NotImplementedError("downgrade is not supported for this migration")
