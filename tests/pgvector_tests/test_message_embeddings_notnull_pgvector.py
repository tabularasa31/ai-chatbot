"""``msg_embedding_not_null_v1`` against real PostgreSQL, in both directions.

The SQLite unit tests pin which branch the migration *chooses*; only PostgreSQL
can show what the chosen branch then *does* — ``ALTER COLUMN ... SET NOT NULL``
has no SQLite equivalent, and the drifted shape being repaired (a nullable
``vector(1536)`` column, created by raw SQL in ``phase4_message_embeddings_v1``)
cannot be produced from the ORM metadata, which has always said ``nullable=False``.

So each test drops the metadata-built table and rebuilds the drifted one by hand,
which is also the only faithful reproduction of what a deployed database holds.

Run: ``make pgvector-only`` (or ``pytest -m pgvector tests/pgvector_tests/``).
"""

from __future__ import annotations

import logging
import re

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from backend.migrations.versions.msg_embedding_not_null_v1 import upgrade
from backend.models import MessageEmbedding

pytestmark = pytest.mark.pgvector

_VECTOR = "[" + ",".join(["0.1"] * 1536) + "]"

_DRIFTED_DDL = """
    DROP TABLE IF EXISTS message_embeddings CASCADE;
    CREATE TABLE message_embeddings (
        message_id uuid PRIMARY KEY,
        tenant_id uuid NOT NULL,
        created_at timestamp NOT NULL DEFAULT now(),
        last_used_at timestamp NOT NULL DEFAULT now()
    );
    ALTER TABLE message_embeddings ADD COLUMN embedding vector(1536);
"""

_TENANT_INDEX = "ix_message_embeddings_tenant_last_used"


def _rebuild_drifted(conn: sa.engine.Connection) -> None:
    for statement in filter(None, (s.strip() for s in _DRIFTED_DDL.split(";"))):
        conn.execute(sa.text(statement))
    # Built from the ORM metadata, not hand-written DDL, so that reordering the
    # model's Index() columns is visible to the planner test below.
    for index in MessageEmbedding.__table__.indexes:
        index.create(conn)
    conn.commit()


def _insert(conn: sa.engine.Connection, *, embedding: str | None) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO message_embeddings (message_id, tenant_id, embedding)"
            " VALUES (gen_random_uuid(), gen_random_uuid(), :e)"
        ),
        {"e": embedding},
    )
    conn.commit()


def _is_nullable(conn: sa.engine.Connection) -> bool:
    return (
        conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns"
                " WHERE table_name = 'message_embeddings'"
                " AND column_name = 'embedding'"
            )
        ).scalar()
        == "YES"
    )


def _run_upgrade(conn: sa.engine.Connection) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        upgrade()
    conn.commit()


def test_clean_table_gets_the_constraint(pg_engine: sa.engine.Engine) -> None:
    with pg_engine.connect() as conn:
        _rebuild_drifted(conn)
        _insert(conn, embedding=_VECTOR)
        assert _is_nullable(conn) is True

        _run_upgrade(conn)

        assert _is_nullable(conn) is False
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, embedding=None)


def test_running_twice_is_a_no_op(pg_engine: sa.engine.Engine) -> None:
    with pg_engine.connect() as conn:
        _rebuild_drifted(conn)
        _insert(conn, embedding=_VECTOR)
        _run_upgrade(conn)
        _run_upgrade(conn)
        assert _is_nullable(conn) is False


def test_null_rows_skip_the_constraint_instead_of_failing_the_deploy(
    pg_engine: sa.engine.Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """The outage mode this migration exists to avoid: it must not raise."""
    with pg_engine.connect() as conn:
        _rebuild_drifted(conn)
        _insert(conn, embedding=_VECTOR)
        _insert(conn, embedding=None)

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _run_upgrade(conn)

        assert _is_nullable(conn) is True
        assert (
            conn.execute(sa.text("SELECT count(*) FROM message_embeddings")).scalar()
            == 2
        )
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM message_embeddings WHERE embedding IS NULL"
                )
            ).scalar()
            == 1
        )
        warnings = "\n".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "1 row(s) have a NULL embedding" in warnings


def _seed_for_planner(conn: sa.engine.Connection) -> str:
    """Enough rows that a whole-index scan is measurably different from a seek."""
    tenant_id = str(conn.execute(sa.text("SELECT gen_random_uuid()")).scalar())
    conn.execute(
        sa.text(
            "INSERT INTO message_embeddings (message_id, tenant_id)"
            " SELECT gen_random_uuid(), CAST(:t AS uuid) FROM generate_series(1, 20)"
        ),
        {"t": tenant_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO message_embeddings (message_id, tenant_id)"
            " SELECT gen_random_uuid(), gen_random_uuid()"
            " FROM generate_series(1, 20000)"
        )
    )
    conn.execute(sa.text("ANALYZE message_embeddings"))
    conn.commit()
    return tenant_id


def _buffers_touched(plan: str) -> int:
    """Pages read by the whole query: the top node's Buffers line is cumulative."""
    line = re.search(r"Buffers: ([^\n]+)", plan)
    assert line is not None, f"no Buffers line in plan:\n{plan}"
    return sum(int(n) for n in re.findall(r"=(\d+)", line.group(1)))


def test_composite_index_serves_lookups_by_tenant_id_alone(
    pg_engine: sa.engine.Engine,
) -> None:
    """Why dropping ``index=True`` on tenant_id costs nothing.

    Two things have to hold, and only the second one has teeth. The composite
    must be *usable* for a tenant_id-only lookup — but Postgres will happily
    read an entire btree whose leading column is ``last_used_at`` and still
    report ``Index Cond: (tenant_id = ...)``, so the index appearing in the
    plan proves nothing on its own. What separates a leading-column seek from
    a full-index scan is how much of the index it touches, so that is what is
    asserted: a handful of pages, not the whole thing.

    ``enable_seqscan = off`` keeps the check about what the index *can* serve
    rather than what the planner happens to prefer, and the filter is a real
    seeded tenant id — a volatile ``gen_random_uuid()`` call can never be an
    index qual, which is what made the original version of this test fail.
    """
    with pg_engine.connect() as conn:
        _rebuild_drifted(conn)
        tenant_id = _seed_for_planner(conn)

        index_pages = conn.execute(
            sa.text(f"SELECT pg_relation_size('{_TENANT_INDEX}') / 8192")
        ).scalar()
        assert index_pages > 50, "seed too small for the page-count check to mean much"

        conn.execute(sa.text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                sa.text(
                    "EXPLAIN (ANALYZE, BUFFERS, COSTS off, TIMING off, SUMMARY off)"
                    " SELECT message_id FROM message_embeddings"
                    " WHERE tenant_id = CAST(:t AS uuid)"
                ),
                {"t": tenant_id},
            )
        )
        conn.rollback()

        assert _TENANT_INDEX in plan, f"composite index unusable for tenant_id:\n{plan}"
        assert f"Index Cond: (tenant_id = '{tenant_id}'::uuid)" in plan, plan
        assert _buffers_touched(plan) < index_pages // 4, (
            f"tenant_id lookup scanned {_buffers_touched(plan)} of {index_pages}"
            f" index pages — {_TENANT_INDEX} no longer leads with tenant_id:\n{plan}"
        )
