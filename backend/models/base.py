from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import declarative_base

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


class UserContext(BaseModel):
    """Identity fields from a signed KYC token; stored on Chat and used in the pipeline."""

    model_config = {"extra": "ignore"}

    user_id: str = Field(..., min_length=1)
    email: str | None = None
    name: str | None = None
    plan_tier: str | None = Field(
        default=None,
        description='e.g. "free" | "starter" | "growth" | "pro" | "enterprise"',
    )
    audience_tag: str | None = None
    company: str | None = None
    locale: str | None = None
