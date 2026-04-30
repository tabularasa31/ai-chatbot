"""Unit tests for DB URL normalization helpers."""

from __future__ import annotations

import pytest

from backend.core.db import _to_async_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Legacy postgres:// is the primary case to fix.
        ("postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        # Already-valid URLs pass through unchanged.
        ("postgresql://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        ("postgresql+psycopg2://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
    ],
)
def test_normalize_database_url_validator(url: str, expected: str) -> None:
    from backend.core.config import Settings

    result = Settings._normalize_database_url(url)
    assert result == expected


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
