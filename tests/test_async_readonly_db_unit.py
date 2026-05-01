"""Unit tests for the read-only async DB wiring.

Covers the kwargs builder and the module-level engine URL binding without
opening connections — opening a session against the SQLite ``:memory:``
readonly engine inside the suite event loop deadlocked on CI. PG
enforcement of writes is covered separately under
``tests/pgvector_tests/test_async_readonly_engine.py``.
"""

from __future__ import annotations

import inspect

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


def test_get_async_readonly_db_is_async_generator_function() -> None:
    """Dependency is the expected shape for FastAPI to consume."""
    assert inspect.isasyncgenfunction(get_async_readonly_db)
