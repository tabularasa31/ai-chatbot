"""Row-level security isolation tests (real PostgreSQL).

Verifies the second isolation contour from backend/core/rls.py: with the
``app.tenant_id`` GUC set, Postgres itself refuses rows of other tenants —
even for deliberately unfiltered ("forgotten WHERE tenant_id") queries.

RLS does not apply to superusers, so these tests create a dedicated
non-superuser role and connect through it. FORCE ROW LEVEL SECURITY on the
tables makes the policies bite for non-superuser owners as well.
"""

from __future__ import annotations

import uuid
from typing import Generator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from backend.core.rls import (
    install_rls_listener,
    reset_tenant_context,
    rls_statements,
    set_tenant_context,
    tenant_context,
)

pytestmark = pytest.mark.pgvector

_RLS_ROLE = "rls_test_app"
_RLS_PASSWORD = "rls-test-password"


@pytest.fixture(scope="function")
def rls_engine(pg_engine: sa.engine.Engine) -> Generator[sa.engine.Engine, None, None]:
    """Engine connected as a non-superuser role, with RLS policies applied."""
    with pg_engine.connect() as conn:
        for stmt in rls_statements():
            conn.execute(text(stmt))
        conn.execute(
            text(
                "DO $$ BEGIN "
                f"CREATE ROLE {_RLS_ROLE} LOGIN PASSWORD '{_RLS_PASSWORD}' "
                "NOSUPERUSER NOBYPASSRLS; "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        )
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_RLS_ROLE}"))
        conn.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE "
                f"ON ALL TABLES IN SCHEMA public TO {_RLS_ROLE}"
            )
        )
        conn.commit()

    url = pg_engine.url.set(username=_RLS_ROLE, password=_RLS_PASSWORD)
    engine = create_engine(url, poolclass=NullPool, future=True)
    install_rls_listener(engine)
    reset_tenant_context()
    try:
        yield engine
    finally:
        reset_tenant_context()
        engine.dispose()
        with pg_engine.connect() as conn:
            conn.execute(text(f"DROP OWNED BY {_RLS_ROLE}"))
            conn.execute(text(f"DROP ROLE IF EXISTS {_RLS_ROLE}"))
            conn.commit()


class _Seed:
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    chat_a: uuid.UUID
    chat_b: uuid.UUID
    doc_a: uuid.UUID
    doc_b: uuid.UUID


@pytest.fixture(scope="function")
def seed(pg_db_session: Session) -> _Seed:
    """Two tenants, each with a chat+message and a document+embedding."""
    from backend.models import (
        Chat,
        Document,
        DocumentStatus,
        DocumentType,
        Embedding,
        Message,
        MessageRole,
        Tenant,
    )

    s = _Seed()
    for label in ("a", "b"):
        tenant = Tenant(name=f"tenant-{label}")
        pg_db_session.add(tenant)
        pg_db_session.flush()
        chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4())
        pg_db_session.add(chat)
        pg_db_session.flush()
        pg_db_session.add(
            Message(chat_id=chat.id, role=MessageRole.user, content=f"secret-{label}")
        )
        doc = Document(
            tenant_id=tenant.id,
            filename=f"kb-{label}.md",
            file_type=DocumentType.markdown,
            status=DocumentStatus.ready,
            parsed_text=f"kb text {label}",
        )
        pg_db_session.add(doc)
        pg_db_session.flush()
        pg_db_session.add(
            Embedding(
                document_id=doc.id,
                chunk_text=f"chunk-{label}",
                vector=[float(label == "b")] * 1536,
                metadata_json={},
            )
        )
        setattr(s, f"tenant_{label}", tenant.id)
        setattr(s, f"chat_{label}", chat.id)
        setattr(s, f"doc_{label}", doc.id)
    pg_db_session.commit()
    return s


def _scalar(conn: sa.Connection, sql: str) -> object:
    return conn.execute(text(sql)).scalar()


def test_raw_sql_scoped_by_guc(rls_engine: sa.engine.Engine, seed: _Seed) -> None:
    """Direct + child-table policies filter raw, unscoped SQL."""
    with rls_engine.connect() as conn:
        conn.execute(text(f"SET app.tenant_id = '{seed.tenant_a}'"))
        assert _scalar(conn, "SELECT count(*) FROM chats") == 1
        assert _scalar(conn, "SELECT count(*) FROM documents") == 1
        # Child tables (no tenant_id column) are scoped via the parent FK.
        contents = conn.execute(text("SELECT content FROM messages")).scalars().all()
        assert contents == ["secret-a"]
        chunks = conn.execute(text("SELECT chunk_text FROM embeddings")).scalars().all()
        assert chunks == ["chunk-a"]


def test_vector_search_scoped(rls_engine: sa.engine.Engine, seed: _Seed) -> None:
    """A retrieval-shaped query with NO tenant filter still cannot cross tenants."""
    query_vec = "[" + ",".join(["1.0"] * 1536) + "]"
    with rls_engine.connect() as conn:
        conn.execute(text(f"SET app.tenant_id = '{seed.tenant_a}'"))
        rows = conn.execute(
            text(
                "SELECT chunk_text FROM embeddings "
                f"ORDER BY vector <=> '{query_vec}'::vector LIMIT 10"
            )
        ).scalars().all()
    # tenant B's chunk is the exact nearest neighbour, but RLS hides it.
    assert rows == ["chunk-a"]


def test_forgotten_orm_filter_is_scoped(
    rls_engine: sa.engine.Engine, seed: _Seed
) -> None:
    """ContextVar + engine listener scope ORM queries with no tenant filter."""
    from backend.models import Chat

    session_factory = sessionmaker(bind=rls_engine, future=True)
    with tenant_context(seed.tenant_a):
        with session_factory() as db:
            chats = db.query(Chat).all()  # deliberately unfiltered
            assert [c.tenant_id for c in chats] == [seed.tenant_a]
    # Context cleared -> fail-open: both tenants visible again.
    with session_factory() as db:
        assert db.query(Chat).count() == 2


def test_cross_tenant_write_blocked(
    rls_engine: sa.engine.Engine, seed: _Seed
) -> None:
    """WITH CHECK: inserting into another tenant's chat raises; updates hit 0 rows."""
    with rls_engine.connect() as conn:
        conn.execute(text(f"SET app.tenant_id = '{seed.tenant_a}'"))
        with pytest.raises(ProgrammingError, match="row-level security"):
            conn.execute(
                text(
                    "INSERT INTO messages (id, chat_id, role, content, feedback, "
                    "created_at, updated_at) VALUES (:id, :chat_id, 'user', 'x', "
                    "'none', now(), now())"
                ),
                {"id": str(uuid.uuid4()), "chat_id": str(seed.chat_b)},
            )
    with rls_engine.connect() as conn:
        conn.execute(text(f"SET app.tenant_id = '{seed.tenant_a}'"))
        result = conn.execute(
            text("UPDATE chats SET tokens_used = 999 WHERE id = :id"),
            {"id": str(seed.chat_b)},
        )
        assert result.rowcount == 0
        result = conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": str(seed.doc_b)})
        assert result.rowcount == 0


def test_no_context_fail_open(rls_engine: sa.engine.Engine, seed: _Seed) -> None:
    """Without tenant context (background jobs, sweeps) all rows stay visible."""
    with rls_engine.connect() as conn:
        assert _scalar(conn, "SELECT count(*) FROM chats") == 2
        assert _scalar(conn, "SELECT count(*) FROM messages") == 2
        assert _scalar(conn, "SELECT count(*) FROM embeddings") == 2


def test_set_tenant_context_applies_to_open_transaction(
    rls_engine: sa.engine.Engine, seed: _Seed
) -> None:
    """set_tenant_context covers the transaction it is called in (auth flow)."""
    from backend.models import Chat

    session_factory = sessionmaker(bind=rls_engine, future=True)
    reset_tenant_context()
    with session_factory() as db:
        # Transaction already open (boundary lookup happened) before resolve.
        assert db.query(Chat).count() == 2
        set_tenant_context(db, seed.tenant_a)
        assert db.query(Chat).count() == 1
    reset_tenant_context()


@pytest_asyncio.fixture(scope="function")
async def rls_async_engine(rls_engine: sa.engine.Engine):
    from sqlalchemy.ext.asyncio import create_async_engine

    url = rls_engine.url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, poolclass=NullPool, future=True)
    install_rls_listener(engine.sync_engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_sync_context_propagates_to_async_session(
    rls_async_engine, seed: _Seed
) -> None:
    """Widget contour: tenant resolved inside run_sync scopes later async queries."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models import Chat

    factory = async_sessionmaker(bind=rls_async_engine, class_=AsyncSession)
    reset_tenant_context()
    async with factory() as db:
        await db.run_sync(lambda s: set_tenant_context(s, seed.tenant_a))
        result = await db.execute(sa.select(Chat))
        chats = result.scalars().all()
        assert [c.tenant_id for c in chats] == [seed.tenant_a]
        await db.commit()
        # New transaction on the same session: the ContextVar set inside
        # run_sync must still be visible (greenlets share the task context).
        result = await db.execute(sa.select(Chat))
        assert len(result.scalars().all()) == 1
    reset_tenant_context()
