"""
Fixtures for pgvector integration tests.

Intentionally isolated in tests/pgvector_tests/ so regular SQLite tests
in tests/ are unaffected.

Run pgvector tests:
    pytest tests/pgvector_tests/ -m pgvector -q

Configure via env (defaults match docker-compose.yml):
    PG_HOST     (default: localhost)
    PG_PORT     (default: 5432)
    PG_USER     (default: postgres)
    PG_PASSWORD (default: password)
    PG_DBNAME   (default: test_pgvector)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Generator
from unittest.mock import AsyncMock, Mock, patch

import psycopg2
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# Ensure backend.* imports resolve from repo root
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Set required env vars before backend imports (mirrors tests/conftest.py)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:?check_same_thread=False")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("ENCRYPTION_KEY", "7b4_zUZivxPZWzIkXbVf3dpQX9Ab22HB51H9Qcrjya8=")

from backend.models import Base  # noqa: E402


def _pg_params() -> dict:
    return {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "user": os.getenv("PG_USER", "postgres"),
        "password": os.getenv("PG_PASSWORD", "password"),
    }


@pytest.fixture(scope="function")
def pg_engine() -> Generator[sa.engine.Engine, None, None]:
    """SQLAlchemy Engine connected to real PostgreSQL with pgvector.

    Creates an isolated test database per test function, then drops it.
    """
    params = _pg_params()
    test_db = os.getenv("PG_DBNAME", "test_pgvector")
    password_part = f":{params['password']}@" if params["password"] else "@"
    url = (
        f"postgresql+psycopg2://{params['user']}{password_part}"
        f"{params['host']}:{params['port']}/{test_db}"
    )

    # Create fresh test database
    admin_conn = psycopg2.connect(**params, dbname="postgres")
    admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin_conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{test_db}"')
        cur.execute(f'CREATE DATABASE "{test_db}"')
    admin_conn.close()

    engine_ = create_engine(url, echo=False, poolclass=NullPool)
    with engine_.connect() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine_)

    os.environ["DATABASE_URL"] = url
    try:
        yield engine_
    finally:
        engine_.dispose()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:?check_same_thread=False"
        # Drop test database, terminate any lingering connections first
        try:
            cleanup = psycopg2.connect(**params, dbname="postgres")
            cleanup.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with cleanup.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{test_db}' AND pid <> pg_backend_pid()"
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{test_db}"')
            cleanup.close()
        except Exception:
            pass


@pytest.fixture(scope="function")
def pg_db_session(pg_engine: sa.engine.Engine) -> Generator[Session, None, None]:
    """Database session bound to the real PostgreSQL engine."""
    PgSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=pg_engine,
        class_=Session,
        future=True,
    )
    session = PgSessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def mock_openai_client() -> Generator[Mock, None, None]:
    """Mock OpenAI client — no real API calls during pgvector tests."""
    mock_client = Mock()

    def _embeddings_create_response(*args: object, **kwargs: object) -> Mock:
        input_val = kwargs.get("input", "")
        count = 1 if isinstance(input_val, str) else len(input_val)
        return Mock(data=[Mock(embedding=[0.1] * 1536) for _ in range(count)])

    mock_client.embeddings.create.side_effect = _embeddings_create_response
    mock_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content="AI response"))],
        usage=Mock(total_tokens=100),
    )
    mock_esc_client = Mock()
    mock_esc_client.chat.completions.create.return_value = Mock(
        choices=[
            Mock(
                message=Mock(
                    content=json.dumps(
                        {
                            "message_to_user": "A support ticket was created for you.",
                            "followup_decision": None,
                        }
                    )
                )
            )
        ],
        usage=Mock(total_tokens=15),
    )
    # Async-capable mock that delegates to the same sync mock so tests that
    # configure mock_openai_client work transparently for both code paths.
    async_mock_client = AsyncMock()

    async def _async_embeddings_create(*args: object, **kwargs: object) -> Mock:
        return mock_client.embeddings.create(*args, **kwargs)

    async def _async_chat_completions_create(*args: object, **kwargs: object) -> Mock:
        return mock_client.chat.completions.create(*args, **kwargs)

    async_mock_client.embeddings.create.side_effect = _async_embeddings_create
    async_mock_client.chat.completions.create.side_effect = _async_chat_completions_create

    with (
        patch("backend.embeddings.service.get_openai_client", return_value=mock_client),
        patch("backend.search.service.get_openai_client", return_value=mock_client),
        patch("backend.search.service.get_async_openai_client", return_value=async_mock_client),
        patch("backend.chat.service.get_openai_client", return_value=mock_client),
        patch("backend.documents.service.get_openai_client", return_value=mock_client, create=True),
        patch(
            "backend.escalation.openai_escalation.get_openai_client",
            return_value=mock_esc_client,
        ),
        patch(
            "backend.search.service._async_rewrite_query_for_retrieval",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield mock_client


@pytest.fixture(autouse=True)
def _reset_widget_rate_limit_key_override() -> Generator[None, None, None]:
    """Clear widget rate-limit test hook so a failed test cannot leak state."""
    yield
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    set_widget_public_rate_limit_key_override(None)


@pytest.fixture(autouse=True)
def _clear_embedding_cache() -> Generator[None, None, None]:
    """Flush the in-process embedding cache before each test."""
    from backend.search import embedding_cache
    embedding_cache.clear()
    yield
    embedding_cache.clear()


@pytest.fixture(scope="function")
def pg_client(
    pg_engine: sa.engine.Engine, pg_db_session: Session
) -> Generator[TestClient, None, None]:
    """FastAPI TestClient wired to real PostgreSQL engine (pgvector enabled)."""
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from backend.core import db as core_db
    from backend.core.db import get_async_db, get_db
    from backend.main import app

    PgSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=pg_engine,
        class_=Session,
        future=True,
    )

    sync_url = pg_engine.url.render_as_string(hide_password=False)
    async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    async_engine = create_async_engine(async_url, future=True)
    async_session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    def override_get_db() -> Generator[Session, None, None]:
        yield pg_db_session

    async def override_get_async_db():
        async with async_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_async_db] = override_get_async_db
    original_engine = core_db.engine
    original_session_local = core_db.SessionLocal
    core_db.engine = pg_engine
    core_db.SessionLocal = PgSessionLocal

    try:
        with TestClient(app) as c:
            yield c
    finally:
        core_db.engine = original_engine
        core_db.SessionLocal = original_session_local
        app.dependency_overrides.clear()
        try:
            asyncio.get_event_loop().run_until_complete(async_engine.dispose())
        except Exception:
            pass
