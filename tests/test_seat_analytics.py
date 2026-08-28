"""Seat analytics: is the seat being taken, and does its holder ever answer?

Two events carry the whole story — ``seat_granted`` and ``seat_released`` —
and these tests pin the parts of them that are easy to get wrong and
impossible to notice afterwards, because a missing analytics event breaks
nothing a user can see:

* the owner/member split, which is the difference between a founder who
  answers customers and one who hires support and never opens the console;
* every way back. A seat handed in by the give-up button, one that leaves with
  a removed member, and one that goes down with its whole workspace all report
  a release — catching only the first would show seats taken and almost never
  returned;
* ``answered``, read *before* the write that would make it unreadable;
* no amount anywhere on either event. Nothing is charged, so a figure would be
  invented — the property sets below are asserted exactly, so adding one is a
  test failure rather than a decision nobody revisits.

The workspace helpers come from ``tests.test_seats`` rather than being copied:
they encode the onboarding sequence a seat depends on (invite, accept, seat
arrives with the acceptance), and two copies of that would drift.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import (
    Chat,
    Message,
    MessageRole,
    OperatorSession,
    OperatorState,
    Tenant,
    User,
)
from backend.models.base import _utcnow
from tests.test_seats import _auth, _make_chat, _make_workspace, _onboard

GRANTED = "seat_granted"
RELEASED = "seat_released"


@pytest.fixture
def seat_events(monkeypatch) -> list[dict]:
    """Every seat event emitted during the test, in order."""
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.seats.events.capture_event", fake_capture)
    return events


def _of(events: list[dict], name: str) -> list[dict]:
    return [e for e in events if e["event"] == name]


def _one(events: list[dict], name: str) -> dict:
    matching = _of(events, name)
    assert len(matching) == 1, matching
    return matching[0]


def _public_id(db: Session, tenant_id: uuid.UUID) -> str:
    return str(db.query(Tenant).filter(Tenant.id == tenant_id).one().public_id)


def _operator_reply(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    operator_user_id: uuid.UUID,
    at,
) -> Message:
    """One answer a person actually wrote, in a conversation of its own.

    A ``messages`` row rather than an ``operator_sessions`` one, because that
    is where the reply is attributed to its author — see
    ``seats.events._ever_answered``. The surrounding stretch is written too, as
    the real ingest path would, so the fixture is not quietly simpler than
    production.
    """
    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        operator_state=OperatorState.bot,
    )
    db.add(chat)
    db.flush()
    db.add(
        OperatorSession(
            tenant_id=tenant_id,
            chat_id=chat.id,
            operator_user_id=operator_user_id,
            joined_at=at,
            first_reply_at=at,
            ended_at=_utcnow(),
        )
    )
    reply = Message(
        chat_id=chat.id,
        role=MessageRole.operator,
        content="Hello, this is a human.",
        operator_user_id=operator_user_id,
        created_at=at,
    )
    db.add(reply)
    db.commit()
    return reply


# ---------------------------------------------------------------------------
# seat_granted
# ---------------------------------------------------------------------------


def test_accepting_an_invitation_reports_a_member_seat(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """A colleague joining is the seat sale; the event says who it was for."""
    ws = _make_workspace(tenant, db_session, email="granted-owner@example.com")
    _onboard(tenant, db_session, ws, email="joins@example.com")

    event = _one(seat_events, GRANTED)
    tenant_public_id = _public_id(db_session, ws.tenant_id)
    assert event["distinct_id"] == tenant_public_id
    assert event["tenant_id"] == tenant_public_id
    assert event["groups"] == {"tenant": tenant_public_id}
    member = db_session.query(User).filter(User.email == "joins@example.com").one()
    assert event["properties"] == {
        "holder": "member",
        "user_id": str(member.id),
        "seats": 1,
    }


def test_an_owner_taking_their_own_seat_is_a_different_holder(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """The founder who sits down to answer customers is its own profile."""
    ws = _make_workspace(tenant, db_session, email="self-seating@example.com")
    _onboard(tenant, db_session, ws, email="colleague@example.com")
    seat_events.clear()

    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    event = _one(seat_events, GRANTED)
    assert event["properties"]["holder"] == "owner"
    assert event["properties"]["user_id"] == str(ws.owner_id)
    # The colleague's seat is still held, so the workspace now has two.
    assert event["properties"]["seats"] == 2


def test_taking_a_seat_twice_reports_one_grant(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """The route is idempotent, and the second call is not a seat sale."""
    ws = _make_workspace(tenant, db_session, email="repeat-taker@example.com")
    tenant.put("/tenants/members/me/seat", headers=ws.auth)
    tenant.put("/tenants/members/me/seat", headers=ws.auth)

    assert len(_of(seat_events, GRANTED)) == 1


def test_an_invitation_nobody_accepts_reports_nothing(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """A pending invitee holds no seat, so there is no grant to report."""
    ws = _make_workspace(tenant, db_session, email="inviter@example.com")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("backend.tenants.members_service.send_email", lambda **kw: None)
        invited = tenant.post(
            "/tenants/members/invite",
            headers=ws.auth,
            json={"email": "never-arrives@example.com"},
        )

    assert invited.status_code == 201, invited.text
    assert seat_events == []


# ---------------------------------------------------------------------------
# seat_released
# ---------------------------------------------------------------------------


def test_giving_up_your_own_seat_reports_the_release(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """How long it was held and whether it ever produced an answer.

    The seat is backdated so both duration properties are pinned to a real
    value rather than to whatever the test took to run.
    """
    ws = _make_workspace(tenant, db_session, email="gives-up@example.com")
    tenant.put("/tenants/members/me/seat", headers=ws.auth)
    owner = db_session.query(User).filter(User.id == ws.owner_id).one()
    owner.seat_granted_at = _utcnow() - timedelta(days=3, hours=2)
    db_session.commit()
    seat_events.clear()

    assert tenant.delete("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    props = _one(seat_events, RELEASED)["properties"]
    assert props == {
        "holder": "owner",
        "user_id": str(ws.owner_id),
        "seats": 0,
        "held_ms": props["held_ms"],
        "held_days": 3,
        "answered": False,
        "reason": "given_up",
    }
    assert 3 * 86_400_000 <= props["held_ms"] < 4 * 86_400_000


def test_giving_up_a_seat_you_do_not_hold_reports_nothing(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """The route is idempotent; a no-op is not a release."""
    ws = _make_workspace(tenant, db_session, email="no-seat@example.com")

    assert tenant.delete("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    assert _of(seat_events, RELEASED) == []


def test_removing_a_member_reports_the_release(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """The common way back. Without this, seats are taken and never returned."""
    ws = _make_workspace(tenant, db_session, email="remover@example.com")
    _onboard(tenant, db_session, ws, email="leaving@example.com")
    member = db_session.query(User).filter(User.email == "leaving@example.com").one()
    member_id = str(member.id)
    seat_events.clear()

    removed = tenant.delete(f"/tenants/members/{member.id}", headers=ws.auth)

    assert removed.status_code == 204, removed.text
    props = _one(seat_events, RELEASED)["properties"]
    assert props == {
        "holder": "member",
        "user_id": member_id,
        "seats": 0,
        "held_ms": props["held_ms"],
        "held_days": 0,
        "answered": False,
        "reason": "member_removed",
    }


def test_removing_a_member_who_answered_says_so(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """Read before the delete, or it could not be read at all.

    ``operator_sessions.operator_user_id`` is ``ON DELETE SET NULL``, so after
    the commit every departing member would look like a seat that never
    answered anybody — inverting the one signal the property exists for.
    """
    ws = _make_workspace(tenant, db_session, email="answered-owner@example.com")
    _onboard(tenant, db_session, ws, email="answers@example.com")
    member = db_session.query(User).filter(User.email == "answers@example.com").one()
    _operator_reply(
        db_session,
        tenant_id=ws.tenant_id,
        operator_user_id=member.id,
        at=_utcnow(),
    )
    seat_events.clear()

    assert tenant.delete(
        f"/tenants/members/{member.id}", headers=ws.auth
    ).status_code == 204

    assert _one(seat_events, RELEASED)["properties"]["answered"] is True


def test_a_reply_from_an_earlier_holding_does_not_count(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """A seat given back and taken again is a new seat.

    ``answered`` is bounded below by the moment this seat was granted, so an
    answer from the previous holding is not evidence about this one — which is
    what keeps "took a seat, never used it, gave it back" visible in somebody
    who once did use one.
    """
    ws = _make_workspace(tenant, db_session, email="second-holding@example.com")
    tenant.put("/tenants/members/me/seat", headers=ws.auth)
    owner = db_session.query(User).filter(User.id == ws.owner_id).one()
    _operator_reply(
        db_session,
        tenant_id=ws.tenant_id,
        operator_user_id=owner.id,
        at=owner.seat_granted_at - timedelta(hours=1),
    )
    seat_events.clear()

    assert tenant.delete("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    assert _one(seat_events, RELEASED)["properties"]["answered"] is False


def test_deleting_the_workspace_returns_every_seat(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """A whole tenant churning must not leave its seats outstanding."""
    ws = _make_workspace(tenant, db_session, email="closing-shop@example.com")
    _onboard(tenant, db_session, ws, email="colleague-a@example.com")
    _onboard(tenant, db_session, ws, email="colleague-b@example.com")
    tenant.put("/tenants/members/me/seat", headers=ws.auth)
    seat_events.clear()

    deleted = tenant.delete(f"/tenants/{ws.tenant_id}", headers=ws.auth)

    assert deleted.status_code in (200, 204), deleted.text
    releases = _of(seat_events, RELEASED)
    assert len(releases) == 3
    assert {e["properties"]["reason"] for e in releases} == {"workspace_deleted"}
    assert {e["properties"]["holder"] for e in releases} == {"owner", "member"}
    # The count walks down to zero rather than reporting "everybody else" three
    # times over.
    assert sorted(e["properties"]["seats"] for e in releases) == [0, 1, 2]


def test_answered_follows_who_wrote_the_reply_not_who_took_the_chat(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """The multi-operator workspace, where the two can be different people.

    ``operator_sessions.operator_user_id`` is whoever opened the stretch and is
    never re-stamped — two colleagues on one thread share one stretch — so
    reading ``answered`` from there credits the reply to the person who took
    the chat and never wrote a word, and reports the person who actually
    answered as a seat that produced nothing. Both wrong at once, silently, in
    exactly the workspace shape the property exists to measure.
    """
    ws = _make_workspace(tenant, db_session, email="two-operators@example.com")
    colleague_token = _onboard(tenant, db_session, ws, email="writes@example.com")
    colleague = db_session.query(User).filter(User.email == "writes@example.com").one()
    colleague_id = str(colleague.id)
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    chat = _make_chat(db_session, ws.tenant_id)

    # The owner claims the conversation and never writes a word in it.
    took = tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth)
    assert took.status_code == 200, took.text
    # The colleague answers the visitor in that same claimed conversation.
    answered = tenant.post(
        f"/operator/chats/{chat.id}/messages",
        headers=_auth(colleague_token),
        json={"text": "Hello, this is a human."},
    )
    assert answered.status_code == 200, answered.text
    # One stretch, opened by the owner, replied to by the colleague.
    stretches = (
        db_session.query(OperatorSession)
        .filter(OperatorSession.chat_id == chat.id)
        .all()
    )
    assert len(stretches) == 1
    assert str(stretches[0].operator_user_id) == str(ws.owner_id)
    assert stretches[0].first_reply_at is not None
    seat_events.clear()

    removed = tenant.delete(f"/tenants/members/{colleague.id}", headers=ws.auth)

    assert removed.status_code == 204, removed.text
    colleague_release = _one(seat_events, RELEASED)
    assert colleague_release["properties"]["user_id"] == colleague_id
    assert colleague_release["properties"]["answered"] is True
    seat_events.clear()

    assert tenant.delete("/tenants/members/me/seat", headers=ws.auth).status_code == 200

    owner_release = _one(seat_events, RELEASED)
    assert owner_release["properties"]["user_id"] == str(ws.owner_id)
    assert owner_release["properties"]["answered"] is False


def test_the_founding_owner_seat_scrub_reports_nothing(
    tenant: TestClient, db_session: Session, seat_events: list[dict]
) -> None:
    """Creating a workspace clears a stale seat, and that is not a release.

    ``create_tenant`` points ``users.tenant_id`` at the new workspace *before*
    scrubbing the seat, so nothing about the row would stop an event being
    attributed to a workspace that never sold it. The only thing keeping it
    quiet is that no call was written — this is what fails if somebody adds one
    for symmetry.
    """
    from backend.core.security import hash_password
    from backend.tenants.service import create_tenant

    # A row that outlived an earlier workspace: detached, still carrying the
    # seat it held there.
    stale = User(
        email="outlived-a-workspace@example.com",
        password_hash=hash_password("SecurePass1!"),
        tenant_id=None,
        is_verified=True,
        seat_granted_at=_utcnow() - timedelta(days=30),
    )
    db_session.add(stale)
    db_session.commit()

    create_tenant(stale.id, "Second Try", db_session)

    db_session.refresh(stale)
    assert stale.seat_granted_at is None
    assert stale.tenant_id is not None
    assert seat_events == []


# ---------------------------------------------------------------------------
# Telemetry can never fail the action it describes
# ---------------------------------------------------------------------------


def test_a_broken_metrics_backend_does_not_break_the_removal(
    tenant: TestClient, db_session: Session, monkeypatch
) -> None:
    """The seat is already gone by the time the event is emitted."""
    ws = _make_workspace(tenant, db_session, email="posthog-down@example.com")
    _onboard(tenant, db_session, ws, email="still-leaves@example.com")
    member = db_session.query(User).filter(User.email == "still-leaves@example.com").one()

    def boom(*args, **kwargs):
        raise RuntimeError("posthog down")

    monkeypatch.setattr("backend.seats.events.capture_event", boom)

    removed = tenant.delete(f"/tenants/members/{member.id}", headers=ws.auth)

    assert removed.status_code == 204, removed.text
    assert tenant.get("/tenants/members", headers=ws.auth).json()["seats"] == 0
