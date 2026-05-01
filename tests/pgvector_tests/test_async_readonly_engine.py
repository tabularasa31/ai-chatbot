"""Verify the async readonly engine wiring blocks writes at the PG level."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _async_url(pg_engine: sa.engine.Engine) -> str:
    sync_url = pg_engine.url.render_as_string(hide_password=False)
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


@pytest.mark.pgvector
@pytest.mark.asyncio
async def test_readonly_engine_allows_select(pg_engine: sa.engine.Engine) -> None:
    engine = create_async_engine(
        _async_url(pg_engine),
        future=True,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            row = await session.execute(sa.text("SELECT 1"))
            assert row.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.pgvector
@pytest.mark.asyncio
async def test_readonly_engine_rejects_update(pg_engine: sa.engine.Engine) -> None:
    """UPDATE on an existing table raises read-only transaction error."""
    async_url = _async_url(pg_engine)

    rw_engine = create_async_engine(async_url, future=True)
    try:
        rw_factory = async_sessionmaker(rw_engine, class_=AsyncSession, expire_on_commit=False)
        async with rw_factory() as session:
            await session.execute(sa.text("CREATE TABLE IF NOT EXISTS _ro_probe (id int)"))
            await session.execute(sa.text("INSERT INTO _ro_probe (id) VALUES (1)"))
            await session.commit()
    finally:
        await rw_engine.dispose()

    ro_engine = create_async_engine(
        async_url,
        future=True,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        ro_factory = async_sessionmaker(ro_engine, class_=AsyncSession, expire_on_commit=False)
        async with ro_factory() as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(sa.text("UPDATE _ro_probe SET id = 2"))
            assert "read-only transaction" in str(exc_info.value).lower()
    finally:
        await ro_engine.dispose()


@pytest.mark.pgvector
@pytest.mark.asyncio
async def test_readonly_engine_rejects_ddl(pg_engine: sa.engine.Engine) -> None:
    engine = create_async_engine(
        _async_url(pg_engine),
        future=True,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(sa.text("CREATE TABLE _ro_ddl_probe (id int)"))
            assert "read-only transaction" in str(exc_info.value).lower()
    finally:
        await engine.dispose()
