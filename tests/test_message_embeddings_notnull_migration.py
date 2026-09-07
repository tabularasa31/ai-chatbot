"""The conditional half of ``msg_embedding_not_null_v1``, exercised directly.

The migration must never fail a deploy: ``SET NOT NULL`` on a column that still
holds NULLs aborts, and on Railway that aborts the release step and crash-loops
the service. So the decision of whether to apply the constraint is a separate,
side-effect-free function reading live state, and it is the part worth pinning:
the "there are NULLs, stand down" branch is precisely the one that never runs
in a healthy database and would otherwise only be discovered in production.

The table is built here by hand rather than from the ORM metadata, because the
model declares ``embedding`` NOT NULL — the drifted, nullable shape this
migration repairs cannot be produced from the model at all.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from backend.migrations.versions.msg_embedding_not_null_v1 import (
    ABSENT,
    ALREADY,
    APPLY,
    BLOCKED,
    embedding_repair_plan,
    upgrade,
)


def _connect(ddl: str | None = None) -> sa.engine.Connection:
    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    if ddl:
        conn.execute(sa.text(ddl))
    return conn


_DRIFTED = """
    CREATE TABLE message_embeddings (
        message_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        embedding TEXT
    )
"""

_REPAIRED = _DRIFTED.replace("embedding TEXT", "embedding TEXT NOT NULL")


def test_empty_table_takes_the_constraint() -> None:
    with _connect(_DRIFTED) as conn:
        assert embedding_repair_plan(conn) == (APPLY, 0)


def test_fully_populated_table_takes_the_constraint() -> None:
    with _connect(_DRIFTED) as conn:
        conn.execute(
            sa.text(
                "INSERT INTO message_embeddings VALUES ('m1', 't1', '[0.1]'),"
                " ('m2', 't1', '[0.2]')"
            )
        )
        assert embedding_repair_plan(conn) == (APPLY, 0)


def test_null_rows_block_the_constraint_and_are_counted() -> None:
    """The branch that keeps a deploy alive: report, do not ALTER."""
    with _connect(_DRIFTED) as conn:
        conn.execute(
            sa.text(
                "INSERT INTO message_embeddings VALUES ('m1', 't1', '[0.1]'),"
                " ('m2', 't1', NULL), ('m3', 't2', NULL)"
            )
        )
        assert embedding_repair_plan(conn) == (BLOCKED, 2)


def test_null_rows_are_left_untouched() -> None:
    """Deleting or rewriting rows is never this migration's decision."""
    with _connect(_DRIFTED) as conn:
        conn.execute(
            sa.text(
                "INSERT INTO message_embeddings VALUES ('m1', 't1', '[0.1]'),"
                " ('m2', 't1', NULL)"
            )
        )
        embedding_repair_plan(conn)
        rows = conn.execute(
            sa.text("SELECT message_id, embedding FROM message_embeddings ORDER BY 1")
        ).all()
        assert rows == [("m1", "[0.1]"), ("m2", None)]


def test_already_repaired_table_is_a_no_op() -> None:
    """Second run on a healthy database: idempotent."""
    with _connect(_REPAIRED) as conn:
        assert embedding_repair_plan(conn) == (ALREADY, 0)


def test_missing_table_is_a_no_op() -> None:
    with _connect() as conn:
        assert embedding_repair_plan(conn) == (ABSENT, 0)


def _run_upgrade(conn: sa.engine.Connection) -> None:
    """Drive the migration's own ``upgrade()`` through a real alembic context."""
    with Operations.context(MigrationContext.configure(conn)):
        upgrade()


def test_upgrade_survives_null_rows_and_says_so(caplog) -> None:
    """The deploy-safety contract: no exception, no data touched, loud warning."""
    with _connect(_DRIFTED) as conn:
        conn.execute(
            sa.text(
                "INSERT INTO message_embeddings VALUES ('m1', 't1', '[0.1]'),"
                " ('m2', 't1', NULL)"
            )
        )
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _run_upgrade(conn)

        assert conn.execute(
            sa.text("SELECT count(*) FROM message_embeddings")
        ).scalar() == 2
        warning = "\n".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "1 row(s) have a NULL embedding" in warning


def test_upgrade_is_a_no_op_on_sqlite_when_clean(caplog) -> None:
    """SQLite gets its NOT NULL from the ORM metadata; nothing to alter, nothing to warn."""
    with _connect(_DRIFTED) as conn:
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _run_upgrade(conn)
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
