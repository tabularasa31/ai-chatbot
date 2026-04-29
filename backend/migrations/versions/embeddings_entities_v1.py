"""Add ``embeddings.entities`` JSONB column for the entity-overlap channel.

Revision ID: embeddings_entities_v1
Revises: link_safety_bot_config_v1

Step 4 of the entity-aware retrieval epic (ClickUp 86exe5pjx).
``extract_entities_from_passage`` (PR #537) emits a list of named entities
per FAQ chunk; this migration adds a column on the ``embeddings`` table to
store that list, plus a GIN index so the Step 5 retriever can answer
"give me chunks whose entities overlap with [...]" with one indexed scan
instead of a full table walk.

Why JSONB and not a separate ``embedding_entities`` join table:
- Hot path is "for query X, find chunks containing any of these entities";
  a single ``WHERE entities ?| array[...]`` against a GIN index beats a
  join → group-by every time.
- The list is authored together with the chunk (by the same NER call),
  has no independent lifecycle, and is strongly bound to the
  ``Embedding.id`` row. A 1-1 sidecar table would buy nothing.
- ``embeddings.metadata_json`` is already JSON/JSONB. No new infra.

Idempotent: skips the column add if it already exists (re-runs after a
partial rollout don't re-error). Default is ``'[]'::jsonb`` so the
column is NOT NULL even on legacy rows that pre-date this migration.

GIN index uses ``jsonb_path_ops`` — smaller and faster than the default
operator class for the ``?|`` containment queries we actually run, at
the cost of supporting only a subset of operators we don't use here.

Per project rules: downgrade is fail-loud. Never run it on prod.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "embeddings_entities_v1"
down_revision = "link_safety_bot_config_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("embeddings")}
    if "entities" not in cols:
        # Use JSONB on PostgreSQL; SQLite (tests) stores as TEXT-encoded JSON.
        if bind.dialect.name == "postgresql":
            op.add_column(
                "embeddings",
                sa.Column(
                    "entities",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=False,
                    server_default=sa.text("'[]'::jsonb"),
                ),
            )
        else:
            op.add_column(
                "embeddings",
                sa.Column(
                    "entities",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                ),
            )

    # GIN index for the ``?|`` (any-of-array) operator. Only on PG;
    # SQLite tests don't have GIN and don't need it (tiny test data).
    if bind.dialect.name == "postgresql":
        existing_indexes = {ix["name"] for ix in insp.get_indexes("embeddings")}
        if "ix_embeddings_entities_gin" not in existing_indexes:
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_embeddings_entities_gin "
                "ON embeddings USING gin (entities jsonb_path_ops)"
            )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    raise NotImplementedError("downgrade is not supported for this migration")
