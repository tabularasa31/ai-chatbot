"""Repair message_embeddings.embedding: apply NOT NULL when the data allows it.

``phase4_message_embeddings_v1`` added the column with raw SQL
(``ALTER TABLE message_embeddings ADD COLUMN embedding vector(1536)``) and no
``NOT NULL``, while the ORM has always declared ``nullable=False``. A row here
without an embedding is meaningless — it can never be a search hit — so
``NOT NULL`` is the intent, and ``answer_cache_v1`` states it explicitly for
its own vector column.

Conditional and idempotent, because we cannot see from here whether a deployed
database holds NULL rows and a failing migration crash-loops the Railway
release step. If any NULL rows exist the constraint is skipped and the count is
logged; nothing is deleted or rewritten — what to do with those rows is a
human's call, not this migration's.

Revision ID: msg_embedding_not_null_v1
Revises: esc_trigger_width_v1
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "msg_embedding_not_null_v1"
down_revision = "esc_trigger_width_v1"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "message_embeddings"
_COLUMN = "embedding"

APPLY = "apply"
BLOCKED = "blocked"
ALREADY = "already"
ABSENT = "absent"


def embedding_repair_plan(bind: sa.engine.Connection) -> tuple[str, int]:
    """Decide whether ``embedding`` can take NOT NULL. Returns (action, null_rows).

    Reads live state only — safe to call on any dialect and any number of times.
    """
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return (ABSENT, 0)
    column = next(
        (c for c in insp.get_columns(_TABLE) if c["name"] == _COLUMN),
        None,
    )
    if column is None:
        return (ABSENT, 0)
    if not column["nullable"]:
        return (ALREADY, 0)
    # EXISTS short-circuits on the first offending row; only the blocked path
    # pays for a full count, and only because the log line quotes the number.
    has_nulls = bind.execute(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE} WHERE {_COLUMN} IS NULL)")
    ).scalar()
    if not has_nulls:
        return (APPLY, 0)
    null_rows = (
        bind.execute(
            sa.text(f"SELECT count(*) FROM {_TABLE} WHERE {_COLUMN} IS NULL")
        ).scalar()
        or 0
    )
    return (BLOCKED, null_rows)


def upgrade() -> None:
    if op.get_context().as_sql:
        logger.info(
            "%s.%s left as-is: this repair inspects live data (it only applies "
            "NOT NULL when no NULL rows exist) and so emits no DDL in offline "
            "--sql mode. Run it against a live database.",
            _TABLE,
            _COLUMN,
        )
        return
    bind = op.get_bind()
    action, null_rows = embedding_repair_plan(bind)

    if action == BLOCKED:
        logger.warning(
            "%s.%s left NULLABLE: %d row(s) have a NULL embedding, so SET NOT NULL "
            "would fail and crash-loop this deploy. Those rows are unusable "
            "(never a search hit). Decide with a human whether to delete or "
            "re-embed them, then re-run this migration — it re-checks and "
            "applies the constraint once the count is zero.",
            _TABLE,
            _COLUMN,
            null_rows,
        )
        return

    if action != APPLY:
        return

    # SQLite cannot ALTER a column's nullability; its schema is built from the
    # ORM metadata, which already declares NOT NULL.
    if bind.dialect.name != "postgresql":
        return

    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {_COLUMN} SET NOT NULL")


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Dropping the constraint would also re-open the drift this repairs.
    raise NotImplementedError("downgrade is not supported for this migration")
