"""Unit tests for the read-only async DB wiring.

Exercises the actual module-level ``async_readonly_engine`` and
``get_async_readonly_db`` dependency under SQLite (where the read-only
mechanism is a no-op). PG enforcement is covered separately under
``tests/pgvector_tests/test_async_readonly_engine.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import (
    _build_async_readonly_engine_kwargs,
    async_readonly_engine,
    get_async_readonly_db,
)


def test_kwargs_postgres_sets_readonly_server_setting() -> None:
    kwargs = _build_async_readonly_engine_kwargs("postgresql+asyncpg://u:p@h:5432/db")
    assert kwargs["connect_args"] == {
        "server_settings": {"default_transaction_read_only": "on"},
    }
    assert kwargs["pool_size"] == 10
    assert kwargs["max_overflow"] == 20


def test_kwargs_sqlite_skips_pool_and_server_settings() -> None:
    kwargs = _build_async_readonly_engine_kwargs("sqlite+aiosqlite:///:memory:")
    assert "connect_args" not in kwargs
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    assert kwargs == {"future": True}


def test_module_engine_uses_configured_url() -> None:
    """Bound async engine inherits the test SQLite URL from settings."""
    assert str(async_readonly_engine.url).startswith("sqlite+aiosqlite://")


@pytest.mark.asyncio
async def test_get_async_readonly_db_yields_async_session() -> None:
    """Dependency yields a usable AsyncSession bound to the readonly engine."""
    agen = get_async_readonly_db()
    session = await agen.__anext__()
    try:
        assert isinstance(session, AsyncSession)
        assert session.bind is async_readonly_engine
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
    finally:
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass
