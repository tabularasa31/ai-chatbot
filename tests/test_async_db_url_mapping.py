"""Unit tests for ``_to_async_url`` — DATABASE_URL → async-driver mapping."""

from __future__ import annotations

import pytest

from backend.core.db import _to_async_url


@pytest.mark.parametrize(
    ("sync_url", "expected"),
    [
        # Postgres variants — including legacy ``postgres://`` from Railway/Heroku.
        ("postgresql://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgresql+psycopg2://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgres://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        # SQLite.
        ("sqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
        ("sqlite:////abs/path.db", "sqlite+aiosqlite:////abs/path.db"),
        # Already-async URLs pass through unchanged.
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_to_async_url(sync_url: str, expected: str) -> None:
    assert _to_async_url(sync_url) == expected
