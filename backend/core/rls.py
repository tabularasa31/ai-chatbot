"""Postgres row-level security (RLS): second isolation contour for tenant data.

Tenant isolation is primarily enforced by ORM filters (``tenant_id == ...``).
RLS is defence-in-depth: once the request's tenant is known, the database
itself refuses rows of other tenants even if a service forgot the filter.

How it works:

- The resolved tenant id lives in a ``ContextVar`` set at the auth boundary
  (JWT dependency, widget bot gate, X-API-Key resolve). Each request task has
  its own context, so values never leak between concurrent requests.
- An engine-level ``begin`` listener emits ``SET LOCAL app.tenant_id`` on
  every new transaction. ``SET LOCAL`` is transaction-scoped, so pooled
  connections cannot carry a stale tenant into the next request.
- Policies are **fail-open when no context is set**: background jobs, cron
  sweeps, Alembic data migrations and auth-boundary lookups keep working
  unchanged. Enforcement kicks in only on request paths that set the context
  — exactly where a forgotten ORM filter would leak data.
- Auth-boundary tables (looked up *before* the tenant is known) are exempt;
  see ``AUTH_BOUNDARY_TABLES``.

Enforcement requires connecting as a **non-superuser** role — Postgres
superusers bypass RLS entirely. Tables use ``FORCE ROW LEVEL SECURITY`` so a
non-superuser table owner is enforced too. The pgvector test suite
(``tests/pgvector_tests/test_rls_isolation.py``) verifies isolation through a
dedicated non-superuser role.

Adding a new tenant-scoped table:
1. Add it to ``TENANT_SCOPED_TABLES`` (or ``CHILD_SCOPED_TABLES`` if it has no
   ``tenant_id`` column and is scoped via a parent FK).
2. Write a new Alembic migration applying ``rls_statements()`` for the new
   table (existing policies are idempotent to re-apply).
``tests/test_rls_registry.py`` fails if a table with a ``tenant_id`` column is
left unclassified.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

# Tables with a tenant_id column, isolated directly.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "background_jobs",
    "chats",
    "contact_sessions",
    "documents",
    "escalation_tickets",
    "gap_analyzer_jobs",
    "gap_clusters",
    "gap_dismissals",
    "gap_doc_topics",
    "gap_questions",
    "guard_events",
    "log_analysis_state",
    "message_embeddings",
    "pii_events",
    "quick_answers",
    "tenant_faq",
    "tenant_profiles",
    "url_sources",
)

# Tables whose tenant_id is nullable by design (NULL = global row, e.g. a
# cross-tenant background job). Global rows stay visible/writable under a
# tenant context — they are not tenant data, so there is nothing to leak.
NULLABLE_TENANT_TABLES: frozenset[str] = frozenset({"background_jobs"})

# Tables without a tenant_id column, isolated via a parent-FK EXISTS policy:
# table -> (fk column on the table, parent table with tenant_id).
CHILD_SCOPED_TABLES: dict[str, tuple[str, str]] = {
    "embeddings": ("document_id", "documents"),
    "gap_question_message_links": ("chat_id", "chats"),
    "messages": ("chat_id", "chats"),
    "url_source_runs": ("source_id", "url_sources"),
}

# Tables intentionally NOT under RLS: they are queried before the tenant is
# known (login by email, JWT user lookup, widget bot public_id resolve,
# X-API-Key hash lookup) — a fail-closed policy would break authentication,
# and a fail-open one adds nothing since these lookups run with no context.
AUTH_BOUNDARY_TABLES: frozenset[str] = frozenset(
    {"bots", "tenant_api_keys", "tenants", "users"}
)

# NULL when the GUC is unset or empty — the fail-open branch of every policy.
_CTX = "NULLIF(current_setting('app.tenant_id', true), '')"


def rls_statements() -> list[str]:
    """DDL enabling RLS + tenant-isolation policies for every covered table.

    Idempotent: safe to re-apply (DROP POLICY IF EXISTS before CREATE).
    Used by the Alembic migration and by the pgvector test fixtures.
    """
    stmts: list[str] = []
    for table in TENANT_SCOPED_TABLES:
        null_branch = " OR tenant_id IS NULL" if table in NULLABLE_TENANT_TABLES else ""
        stmts += _policy_ddl(
            table,
            f"{_CTX} IS NULL{null_branch} OR tenant_id = {_CTX}::uuid",
        )
    for table, (fk_col, parent) in CHILD_SCOPED_TABLES.items():
        stmts += _policy_ddl(
            table,
            f"{_CTX} IS NULL OR EXISTS ("
            f"SELECT 1 FROM {parent} p "
            f"WHERE p.id = {table}.{fk_col} AND p.tenant_id = {_CTX}::uuid)",
        )
    return stmts


def _policy_ddl(table: str, using: str) -> list[str]:
    policy = f"{table}_tenant_isolation"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {policy} ON {table}",
        f"CREATE POLICY {policy} ON {table} FOR ALL USING ({using})",
    ]


_current_tenant_id: ContextVar[str | None] = ContextVar(
    "rls_current_tenant_id", default=None
)


def get_tenant_context() -> str | None:
    """Canonical string uuid of the current request's tenant, if resolved."""
    return _current_tenant_id.get()


def set_tenant_context(db: Session | None, tenant_id: uuid.UUID | str) -> None:
    """Record the resolved tenant for RLS enforcement.

    Sets the ContextVar (picked up by the engine ``begin`` listener on every
    subsequent transaction) and, when ``db`` is an open Postgres session,
    also applies ``SET LOCAL`` to the *current* transaction so statements
    later in the same transaction are covered too.

    The uuid round-trip both canonicalizes the value and guarantees it is
    safe to interpolate into ``SET LOCAL`` (no bind params for SET).
    """
    value = str(uuid.UUID(str(tenant_id)))
    _current_tenant_id.set(value)
    if db is not None and db.get_bind().dialect.name == "postgresql":
        db.execute(text(f"SET LOCAL app.tenant_id = '{value}'"))


async def async_set_tenant_context(
    db: AsyncSession | None, tenant_id: uuid.UUID | str
) -> None:
    """Async twin of :func:`set_tenant_context`."""
    value = str(uuid.UUID(str(tenant_id)))
    _current_tenant_id.set(value)
    if db is not None and db.get_bind().dialect.name == "postgresql":
        await db.execute(text(f"SET LOCAL app.tenant_id = '{value}'"))


def reset_tenant_context() -> None:
    _current_tenant_id.set(None)


def clear_tenant_context(db: Session | None = None) -> None:
    """Explicit platform-wide bypass: drop the tenant context for this request.

    For platform-admin operations (cross-tenant metrics, retention cleanup)
    that must see every tenant's rows after ``get_current_user`` has already
    scoped the request. Clears the ContextVar (future transactions) and, when
    ``db`` is an open Postgres session, blanks the GUC in the *current*
    transaction too — an empty string hits the fail-open ``NULLIF`` branch of
    every policy.
    """
    _current_tenant_id.set(None)
    if db is not None and db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL app.tenant_id = ''"))


@contextmanager
def tenant_context(tenant_id: uuid.UUID | str) -> Iterator[None]:
    """Scope RLS tenant context around a block (background jobs, scripts)."""
    token = _current_tenant_id.set(str(uuid.UUID(str(tenant_id))))
    try:
        yield
    finally:
        _current_tenant_id.reset(token)


def install_rls_listener(engine: Engine) -> None:
    """Emit ``SET LOCAL app.tenant_id`` at the start of every transaction.

    No-op on non-Postgres engines (SQLite tests). For async engines pass
    ``async_engine.sync_engine``.
    """
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "begin")
    def _set_tenant_guc(conn: Connection) -> None:
        tid = _current_tenant_id.get()
        if tid is not None:
            conn.exec_driver_sql(f"SET LOCAL app.tenant_id = '{tid}'")


def _install_app_listeners() -> None:
    from backend.core.db import async_engine, async_readonly_engine, engine

    for eng in (engine, async_engine.sync_engine, async_readonly_engine.sync_engine):
        install_rls_listener(eng)


_install_app_listeners()
