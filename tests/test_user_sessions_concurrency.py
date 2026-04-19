from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from backend.models import User, ContactSession
from backend.contact_sessions import service as contact_session_service
from tests.test_models import _create_client, _create_user


def _session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        future=True,
    )


def _active_sessions(db: Session, *, tenant_id: uuid.UUID, contact_id: str) -> list[ContactSession]:
    return (
        db.query(ContactSession)
        .filter(
            ContactSession.tenant_id == tenant_id,
            ContactSession.contact_id == contact_id,
            ContactSession.session_ended_at.is_(None),
        )
        .order_by(ContactSession.session_started_at.asc(), ContactSession.id.asc())
        .all()
    )


def test_start_user_session_two_writer_race_returns_winner_row(engine, db_session: Session, monkeypatch) -> None:
    user = _create_user(db_session, email="user-session-race-a@example.com")
    tenant = _create_client(db_session, user, name="User Session Race A")
    user_context = {"user_id": "u1", "email": "user-session-race-a@example.com"}
    session_factory = _session_factory(engine)
    with session_factory() as winner_db:
        winner_row = contact_session_service.start_user_session(
            winner_db,
            tenant_id=tenant.id,
            user_context=user_context,
        )
        winner_db.commit()
        winner_id = winner_row.id if winner_row is not None else None

    def _no_close(*args, **kwargs):
        return None

    def _raise_duplicate(*args, **kwargs):
        raise contact_session_service.IntegrityError("stmt", {}, Exception("duplicate active row"))

    monkeypatch.setattr(contact_session_service, "_close_active_user_sessions", _no_close)
    monkeypatch.setattr(contact_session_service, "_create_user_session_row", _raise_duplicate)

    row = contact_session_service.start_user_session(
        db_session,
        tenant_id=tenant.id,
        user_context=user_context,
    )
    db_session.commit()

    assert winner_id is not None
    assert row is not None
    assert row.id == winner_id

    active_rows = _active_sessions(db_session, tenant_id=tenant.id, contact_id="u1")
    assert len(active_rows) == 1
    assert active_rows[0].id == winner_id


def test_start_user_session_close_then_race_creates_one_new_active_session(
    engine,
    db_session: Session,
    monkeypatch,
) -> None:
    user = _create_user(db_session, email="user-session-race-b@example.com")
    tenant = _create_client(db_session, user, name="User Session Race B")
    user_context = {"user_id": "u1", "email": "user-session-race-b@example.com"}
    old_row = contact_session_service.start_user_session(
        db_session,
        tenant_id=tenant.id,
        user_context=user_context,
    )
    db_session.commit()
    assert old_row is not None

    session_factory = _session_factory(engine)
    original_create = contact_session_service._create_user_session_row
    original_close = contact_session_service._close_active_user_sessions
    winner_id: uuid.UUID | None = None

    def _no_close(*args, **kwargs):
        return None

    def _inject_conflict(*args, **kwargs):
        nonlocal winner_id
        # Simulate the competing writer by temporarily restoring the original
        # helpers, creating the winning replacement row in a separate session,
        # then reinstating the patched conflict path for the outer caller.
        with session_factory() as db:
            monkeypatch.setattr(contact_session_service, "_create_user_session_row", original_create)
            monkeypatch.setattr(contact_session_service, "_close_active_user_sessions", original_close)
            row = contact_session_service.start_user_session(
                db,
                tenant_id=tenant.id,
                user_context=user_context,
            )
            db.commit()
            winner_id = row.id if row is not None else None
        monkeypatch.setattr(contact_session_service, "_create_user_session_row", _inject_conflict)
        monkeypatch.setattr(contact_session_service, "_close_active_user_sessions", _no_close)
        raise contact_session_service.IntegrityError("stmt", {}, Exception("duplicate active row"))

    monkeypatch.setattr(contact_session_service, "_close_active_user_sessions", _no_close)
    monkeypatch.setattr(contact_session_service, "_create_user_session_row", _inject_conflict)

    row = contact_session_service.start_user_session(
        db_session,
        tenant_id=tenant.id,
        user_context=user_context,
    )
    db_session.commit()

    assert winner_id is not None
    assert row is not None
    assert row.id == winner_id

    db_session.expire_all()
    refreshed_old = db_session.get(ContactSession, old_row.id)
    assert refreshed_old is not None
    assert refreshed_old.session_ended_at is not None

    active_rows = _active_sessions(db_session, tenant_id=tenant.id, contact_id="u1")
    assert len(active_rows) == 1
    assert active_rows[0].id == winner_id
    assert active_rows[0].id != old_row.id


def test_start_user_session_savepoint_rollback_preserves_outer_transaction(
    engine,
    db_session: Session,
    monkeypatch,
) -> None:
    user = _create_user(db_session, email="user-session-race-c@example.com")
    tenant = _create_client(db_session, user, name="User Session Race C")
    user_context = {"user_id": "u1", "email": "user-session-race-c@example.com"}
    old_row = contact_session_service.start_user_session(
        db_session,
        tenant_id=tenant.id,
        user_context=user_context,
    )
    db_session.commit()
    assert old_row is not None

    session_factory = _session_factory(engine)
    unrelated_email = "user-session-race-c-unrelated@example.com"

    def _raise_duplicate(*args, **kwargs):
        raise contact_session_service.IntegrityError("stmt", {}, Exception("duplicate active row"))

    monkeypatch.setattr(contact_session_service, "_create_user_session_row", _raise_duplicate)

    with session_factory() as db:
        unrelated_user = User(email=unrelated_email, password_hash="hashed")
        db.add(unrelated_user)
        recovered_row = contact_session_service.start_user_session(
            db,
            tenant_id=tenant.id,
            user_context=user_context,
        )
        db.commit()
        winner_id = recovered_row.id if recovered_row is not None else None
        unrelated_user_id = unrelated_user.id

    assert winner_id is not None
    assert winner_id == old_row.id

    db_session.expire_all()
    preserved_user = db_session.get(User, unrelated_user_id)
    assert preserved_user is not None
    assert preserved_user.email == unrelated_email

    refreshed_old = db_session.get(ContactSession, old_row.id)
    assert refreshed_old is not None
    assert refreshed_old.session_ended_at is None

    active_rows = _active_sessions(db_session, tenant_id=tenant.id, contact_id="u1")
    assert len(active_rows) == 1
    assert active_rows[0].id == winner_id
