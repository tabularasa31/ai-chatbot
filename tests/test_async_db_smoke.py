"""Smoke test for the async DB contour.

Exercises the parallel async path (``get_async_db`` / ``AsyncSession``) without
touching the existing sync contour. Acts as the canary that ``aiosqlite`` and
the async engine wiring are healthy.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_async_session_executes_query(async_db_session: AsyncSession) -> None:
    result = await async_db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_async_session_sees_metadata_tables(async_db_session: AsyncSession) -> None:
    """Tables registered on Base.metadata are created on the async engine."""
    result = await async_db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    )
    tables = {row[0] for row in result.all()}
    assert "users" in tables
