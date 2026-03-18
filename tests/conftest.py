from __future__ import annotations

from typing import Generator
import os
import sys

# Set test env before any backend imports
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:?check_same_thread=False")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

# Patch create_engine for SQLite (pool_size/max_overflow not supported)
import sqlalchemy as _sa
_original_create_engine = _sa.create_engine


def _patched_create_engine(url, **kwargs):
    if "sqlite" in str(url):
        kwargs.pop("max_overflow", None)
        kwargs.pop("pool_size", None)
        url_str = str(url)
        if "check_same_thread" not in url_str:
            url = url_str + ("&" if "?" in url_str else "?") + "check_same_thread=False"
    return _original_create_engine(url, **kwargs)


_sa.create_engine = _patched_create_engine

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# добавляем корень проекта в PYTHONPATH
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.models import Base


@pytest.fixture(scope="function")
def engine() -> Generator[Engine, None, None]:
    """Создаёт движок SQLite для тестов."""
    import tempfile
    import os as _os
    fd, path = tempfile.mkstemp(suffix=".db")
    _os.close(fd)
    url = f"sqlite:///{path}?check_same_thread=False"
    os.environ["DATABASE_URL"] = url
    engine_ = create_engine(url, echo=False, future=True)

    @event.listens_for(engine_, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[override]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine_)
    try:
        yield engine_
    finally:
        engine_.dispose()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:?check_same_thread=False"
        try:
            _os.unlink(path)
        except OSError:
            pass


@pytest.fixture(scope="function")
def db_session(engine: Engine) -> Generator[Session, None, None]:
    """Предоставляет сессию БД для каждого теста."""
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        future=True,
    )
    session = TestingSessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(engine: Engine, db_session: Session) -> Generator[TestClient, None, None]:
    """Test client with auth routes, using test database."""
    from backend.core import db as core_db
    from backend.core.db import get_db
    from backend.main import app

    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        future=True,
    )

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    original_engine = core_db.engine
    original_session_local = core_db.SessionLocal
    core_db.engine = engine
    core_db.SessionLocal = TestingSessionLocal

    try:
        with TestClient(app) as c:
            yield c
    finally:
        core_db.engine = original_engine
        core_db.SessionLocal = original_session_local
        app.dependency_overrides.clear()

