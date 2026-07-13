"""Add partial created_at index for the guard_events retention purge.

The daily purge (backend/jobs/guard_events_purge.py) deletes stale rows with a
predicate on ``label`` + ``created_at`` and NO ``tenant_id`` filter, so the
existing composite ``(tenant_id, created_at)`` index cannot serve it. Without a
matching index each 1k-row batch rescans most of this write-heavy table.

This partial index on ``created_at WHERE label IS NULL`` covers the purge's hot
branch (unlabeled rows are the overwhelming majority) so the job seeks the
oldest eligible rows instead of a full scan. The sparse labeled branch
(``label IS NOT NULL``) stays served by ``ix_guard_events_label``.

Built with ``CREATE INDEX CONCURRENTLY`` so the build takes no ``ACCESS
EXCLUSIVE`` lock on a table written on every chat turn. CONCURRENTLY cannot run
inside a transaction, so the statement runs in an ``autocommit_block``.
Idempotent via ``IF NOT EXISTS`` (repair-safe replay).

Revision ID: guard_events_purge_index_v1
Revises: guard_events_rls_v1
"""

from __future__ import annotations

from alembic import op

revision = "guard_events_purge_index_v1"
down_revision = "guard_events_rls_v1"
branch_labels = None
depends_on = None

_INDEX = "ix_guard_events_purge_unlabeled"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # SQLite (tests) builds every index from the model metadata via
        # create_all; nothing to do here, and CONCURRENTLY is Postgres-only.
        return
    # CONCURRENTLY cannot run inside a transaction; autocommit_block temporarily
    # suspends Alembic's per-migration transaction for this statement.
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} "
            "ON guard_events (created_at) WHERE label IS NULL"
        )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    raise NotImplementedError("downgrade is not supported for this migration")
