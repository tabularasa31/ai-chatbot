from __future__ import annotations

from typing import Generator
import os
import sys

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# добавляем корень проекта в PYTHONPATH, чтобы импортировать пакет backend
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.models import Base


@pytest.fixture(scope="function")
def engine() -> Engine:
    """Создаёт движок SQLite in-memory для тестов."""
    engine_ = create_engine(
        "sqlite:///:memory:",
        echo=False,
        future=True,
    )

    @event.listens_for(engine_, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[override]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine_)
    return engine_


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

