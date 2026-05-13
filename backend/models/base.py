from __future__ import annotations

import datetime as dt
import logging

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, declarative_base

logger = logging.getLogger(__name__)

Base = declarative_base()


# Map PostgreSQL-specific types to SQLite-compatible equivalents for tests.
@compiles(PG_UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "CHAR(36)"


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "TEXT"


@compiles(Vector, "sqlite")
def compile_vector_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "TEXT"  # Store as text in SQLite (tests only)


def _utcnow() -> dt.datetime:
    # Naive UTC: every column using this default is declared as ``DateTime``
    # without ``timezone=True`` (i.e. ``TIMESTAMP WITHOUT TIME ZONE`` in
    # Postgres). psycopg2 silently drops ``tzinfo`` on insert, but asyncpg
    # rejects tz-aware values for naive columns with
    # ``can't subtract offset-naive and offset-aware datetimes``.
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _strip_tzinfo_for_naive_datetime_columns(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    """Defense-in-depth: strip ``tzinfo`` from values bound for naive
    ``DateTime`` columns at flush time.

    Every ``DateTime`` column in this project is naive (``TIMESTAMP WITHOUT
    TIME ZONE``); see ``_utcnow`` above for the rationale. psycopg2 silently
    drops ``tzinfo`` on aware values, which is why the sync path "just
    works"; asyncpg refuses the coercion and raises ``DataError`` →
    ``PendingRollbackError`` → 500 on the widget chat path (see PR #680 for
    the production trace).

    This listener inspects every new/dirty ORM object before flush and
    rewrites aware values on naive columns to their naive equivalent. It
    only normalises rather than rejecting so legacy callers that still hand
    in aware datetimes degrade safely; the manual fixes elsewhere in the
    codebase use ``_utcnow`` directly to avoid the listener entirely.

    Limitations:
      * Core-level ``Query.update({"col": aware_value})`` and ``sa.update``
        bypass the ORM unit-of-work and therefore bypass this listener.
        Those call sites must use ``_utcnow`` directly; the audit in PR
        #682 fixed the known ones.
      * Server-side ``onupdate=`` callables are unaffected — they already
        flow through ``_utcnow``.
    """
    # ``IdentitySet.union`` avoids materialising two intermediate lists; the
    # result is iterable directly.
    targets = session.new.union(session.dirty)
    if not targets:
        return
    for obj in targets:
        # ``raiseerr=False`` returns ``None`` instead of raising
        # ``NoInspectionAvailable`` for objects we cannot inspect (e.g. ad-hoc
        # non-mapped instances surfaced via attribute events).
        inspection = sa_inspect(obj, raiseerr=False)
        if inspection is None:
            continue
        mapper = inspection.mapper
        for col in mapper.columns:
            col_type = getattr(col, "type", None)
            if not isinstance(col_type, DateTime):
                continue
            if getattr(col_type, "timezone", False):
                # Aware column — keep tzinfo as-is.
                continue
            value = getattr(obj, col.key, None)
            if value is None:
                continue
            tz = getattr(value, "tzinfo", None)
            if tz is None:
                continue
            # Convert to UTC BEFORE stripping ``tzinfo``. Plain
            # ``value.replace(tzinfo=None)`` preserves wall time, which
            # silently corrupts the instant for non-UTC aware inputs (e.g.
            # ``2026-05-13T10:00:00-04:00`` → ``2026-05-13T10:00:00`` stored
            # as naive UTC = 4 hours off). ``astimezone(UTC)`` normalises
            # the instant first; subsequent ``replace(tzinfo=None)`` then
            # strips the tag without losing information. Project callers
            # already pass UTC, but the listener exists as a defense layer
            # and must handle any aware input safely.
            normalized = value.astimezone(dt.UTC).replace(tzinfo=None)
            logger.debug(
                "naive_datetime_listener: normalising tzinfo on %s.%s "
                "(input_tz=%s)",
                type(obj).__name__,
                col.key,
                tz,
            )
            setattr(obj, col.key, normalized)


event.listen(Session, "before_flush", _strip_tzinfo_for_naive_datetime_columns)


