from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.models import UserSession
from backend.user_sessions import service as user_session_service
from tests.test_models import _create_client, _create_user


@pytest.mark.pgvector
def test_start_user_session_two_writer_race_returns_winner_row_on_postgres(
    pg_engine,
    pg_db_session: Session,
    monkeypatch,
) -> None:
    user = _create_user(pg_db_session, email="user-session-race-pg@example.com")
    client = _create_client(pg_db_session, user, name="User Session Race PG")
    user_context = {"user_id": "u1", "email": "user-session-race-pg@example.com"}
    barrier = threading.Barrier(2)
    original_create = user_session_service._create_user_session_row

    def _barrier_create(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(user_session_service, "_create_user_session_row", _barrier_create)
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=pg_engine,
        class_=Session,
        future=True,
    )

    def _worker() -> uuid.UUID | None:
        with session_factory() as db:
            row = user_session_service.start_user_session(
                db,
                client_id=client.id,
                user_context=user_context,
            )
            db.commit()
            return row.id if row is not None else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_worker)
        second = executor.submit(_worker)
        ids = [first.result(timeout=10), second.result(timeout=10)]

    assert ids[0] is not None
    assert ids[1] is not None
    assert ids[0] == ids[1]

    pg_db_session.expire_all()
    active_rows = (
        pg_db_session.query(UserSession)
        .filter(
            UserSession.client_id == client.id,
            UserSession.user_id == "u1",
            UserSession.session_ended_at.is_(None),
        )
        .all()
    )
    assert len(active_rows) == 1
    assert active_rows[0].id == ids[0]
