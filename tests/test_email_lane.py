"""The inbound e-mail lane: an operator answering from their mailbox.

The behaviours worth protecting, in the order they matter:

* a workspace with no seat sees no change at all — same ``Reply-To``, same
  straight-to-the-visitor path;
* a seat holder's reply lands in the chat thread *and* in the visitor's inbox;
* a reply from anybody else is forwarded to the visitor and never refused;
* the visitor's replies reach the operator while the chat is live;
* the endpoint refuses a missing path secret and a token matching no ticket.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth.roles import ROLE_OPERATOR
from backend.core.config import settings
from backend.email.inbound import (
    InboundOutcome,
    handle_inbound_reply,
    parse_brevo_payload,
)
from backend.email.reply_lane import (
    escalation_reply_to,
    reply_address,
    token_from_recipients,
)
from backend.escalation.service import (
    _notify_tenant_new_ticket,
    resolve_ticket,
)
from backend.models import (
    Chat,
    EscalationStatus,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    OperatorState,
    Tenant,
    User,
)
from backend.models.base import _utcnow
from tests.conftest import register_and_verify_user, set_client_openai_key

_SECRET = "inbound-secret-for-tests"
_DOMAIN = "reply.getchat9.live"


@pytest.fixture(autouse=True)
def _wire_the_lane(monkeypatch: pytest.MonkeyPatch):
    """Configure the lane for every test in this module.

    Without the secret the lane is not wired at all — that state has its own
    test rather than being the default here.
    """
    monkeypatch.setattr(settings, "inbound_email_secret", _SECRET)
    monkeypatch.setattr(settings, "inbound_email_domain", _DOMAIN)
    monkeypatch.setattr(settings, "EMAIL_FROM", "noreply@getchat9.live")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _workspace(
    client: TestClient, db: Session, *, email: str, name: str, seated: bool
) -> tuple[str, uuid.UUID]:
    """A verified owner and their workspace, seated or not."""
    token = register_and_verify_user(client, db, email=email)
    resp = client.post(
        "/tenants", headers={"Authorization": f"Bearer {token}"}, json={"name": name}
    )
    assert resp.status_code == 201, resp.text
    set_client_openai_key(client, token)
    if seated:
        seat = client.put(
            "/tenants/members/me/seat", headers={"Authorization": f"Bearer {token}"}
        )
        assert seat.status_code == 200, seat.text
    return token, uuid.UUID(resp.json()["id"])


def _ticket(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    chat_id: uuid.UUID | None = None,
    number: str = "ESC-9001",
    status: EscalationStatus = EscalationStatus.open,
    user_email: str = "visitor@example.com",
) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number=number,
        primary_question="How do refunds work?",
        trigger=EscalationTrigger.user_request,
        status=status,
        user_email=user_email,
        chat_id=chat_id,
        notification_message_id="<notify-1@brevo>",
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def _chat(db: Session, tenant_id: uuid.UUID, **kwargs) -> Chat:
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), **kwargs)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _colleague(
    db: Session, tenant_id: uuid.UUID, *, email: str, seated: bool
) -> User:
    user = User(
        email=email,
        password_hash="x",
        role=ROLE_OPERATOR,
        is_verified=True,
        tenant_id=tenant_id,
        seat_granted_at=_utcnow() if seated else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _brevo_item(
    *,
    to: str,
    sender: str,
    extracted: str | None = "Sure — within 14 days.",
    raw_text: str = "Sure — within 14 days.\n\nOn Mon, we wrote:\n> original",
    signature: str = "",
    in_reply_to: str = "<notify-1@brevo>",
) -> dict:
    return {
        "items": [
            {
                "From": {"Name": "Ann", "Address": sender},
                "To": [{"Name": "", "Address": to}],
                "Subject": "Re: [ESC-9001] How do refunds work?",
                "InReplyTo": in_reply_to,
                "ExtractedMarkdownMessage": extracted,
                "ExtractedMarkdownSignature": signature,
                "RawTextBody": raw_text,
                "Headers": {"References": in_reply_to},
            }
        ]
    }


def _post_inbound(client: TestClient, payload: dict, *, secret: str = _SECRET):
    return client.post(f"/email/inbound/{secret}", json=payload)


# --------------------------------------------------------------------------
# Outbound: which Reply-To the notification carries
# --------------------------------------------------------------------------


def test_seatless_workspace_keeps_the_visitors_address(
    tenant: TestClient, db_session: Session
) -> None:
    """A tenant with no seat sees no change at all — acceptance criterion 1."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="seatless@example.com", name="Seatless", seated=False
    )
    workspace = db_session.query(Tenant).filter(Tenant.id == tenant_id).one()
    ticket = _ticket(db_session, tenant_id)

    with patch("backend.escalation.service.send_email", return_value="<id@brevo>") as send:
        assert _notify_tenant_new_ticket(workspace, ticket, db_session) is True

    assert send.call_args.kwargs["reply_to"] == "visitor@example.com"
    db_session.refresh(ticket)
    assert ticket.reply_token is None


def test_seated_workspace_gets_our_token_address(
    tenant: TestClient, db_session: Session
) -> None:
    _token, tenant_id = _workspace(
        tenant, db_session, email="seated@example.com", name="Seated", seated=True
    )
    workspace = db_session.query(Tenant).filter(Tenant.id == tenant_id).one()
    ticket = _ticket(db_session, tenant_id)

    with patch("backend.escalation.service.send_email", return_value="<id@brevo>") as send:
        assert _notify_tenant_new_ticket(workspace, ticket, db_session) is True

    db_session.refresh(ticket)
    assert ticket.reply_token
    assert send.call_args.kwargs["reply_to"] == reply_address(ticket.reply_token)


def test_unwired_lane_keeps_the_visitors_address(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No webhook secret means no mailbox to receive on, so no token address."""
    monkeypatch.setattr(settings, "inbound_email_secret", None)
    _token, tenant_id = _workspace(
        tenant, db_session, email="unwired@example.com", name="Unwired", seated=True
    )
    ticket = _ticket(db_session, tenant_id)

    assert escalation_reply_to(ticket, db_session) == "visitor@example.com"
    assert ticket.reply_token is None


def test_the_token_survives_re_notification(
    tenant: TestClient, db_session: Session
) -> None:
    """A repeat notify must not invalidate an address already in an inbox."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="stable@example.com", name="Stable", seated=True
    )
    ticket = _ticket(db_session, tenant_id)

    first = escalation_reply_to(ticket, db_session)
    second = escalation_reply_to(ticket, db_session)
    assert first == second


# --------------------------------------------------------------------------
# Address parsing
# --------------------------------------------------------------------------


def test_token_is_read_out_of_the_recipient_address() -> None:
    assert token_from_recipients([f"reply+abc123@{_DOMAIN}"]) == "abc123"
    assert token_from_recipients([f"Ann <reply+abc123@{_DOMAIN}>"]) == "abc123"
    # Another domain is somebody else's mail, not a malformed token.
    assert token_from_recipients(["reply+abc123@example.com"]) is None
    assert token_from_recipients([f"support@{_DOMAIN}"]) is None
    assert token_from_recipients([]) is None


def test_brevo_extracted_body_is_preferred_over_the_raw_text() -> None:
    """No quote-stripping heuristics: Brevo already did the separation."""
    [reply] = parse_brevo_payload(
        _brevo_item(to=f"reply+tok@{_DOMAIN}", sender="ann@agency.example")
    )
    assert reply.text == "Sure — within 14 days."
    assert "On Mon, we wrote:" not in reply.text


def test_the_raw_text_is_the_fallback_when_brevo_did_not_split() -> None:
    [reply] = parse_brevo_payload(
        _brevo_item(
            to=f"reply+tok@{_DOMAIN}",
            sender="ann@agency.example",
            extracted=None,
            raw_text="Plain body",
        )
    )
    assert reply.text == "Plain body"


def test_a_nonsense_payload_yields_nothing_rather_than_raising() -> None:
    assert parse_brevo_payload("not a payload") == []
    assert parse_brevo_payload({"items": ["not an item"]}) == []


# --------------------------------------------------------------------------
# Inbound: refusals
# --------------------------------------------------------------------------


def test_a_wrong_path_secret_is_refused(tenant: TestClient) -> None:
    resp = _post_inbound(
        tenant,
        _brevo_item(to=f"reply+tok@{_DOMAIN}", sender="ann@agency.example"),
        secret="not-the-secret",
    )
    assert resp.status_code == 404


def test_an_unconfigured_lane_refuses_everything(
    tenant: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "inbound_email_secret", None)
    resp = _post_inbound(
        tenant, _brevo_item(to=f"reply+tok@{_DOMAIN}", sender="ann@agency.example")
    )
    assert resp.status_code == 404


def test_a_token_matching_no_ticket_is_refused(tenant: TestClient) -> None:
    resp = _post_inbound(
        tenant,
        _brevo_item(to=f"reply+nosuchtoken@{_DOMAIN}", sender="ann@agency.example"),
    )
    assert resp.status_code == 404


def test_a_revoked_token_is_refused(tenant: TestClient, db_session: Session) -> None:
    """Resolving the request kills its reply address — that is the revocation."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="revoke@example.com", name="Revoke", seated=True
    )
    ticket = _ticket(db_session, tenant_id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()
    token = ticket.reply_token
    assert token and token in address

    resolve_ticket(ticket.id, tenant_id, "done", db_session)

    resp = _post_inbound(
        tenant, _brevo_item(to=address, sender="ann@agency.example")
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Inbound: attribution
# --------------------------------------------------------------------------


def test_a_seat_holders_reply_enters_the_thread_and_reaches_the_visitor(
    tenant: TestClient, db_session: Session
) -> None:
    """Acceptance criteria 2 and 3, in one pass."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-ingest@example.com", name="Ingest", seated=True
    )
    operator = _colleague(
        db_session, tenant_id, email="ann@agency.example", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="Ann@Agency.example")
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]

    db_session.expire_all()
    rows = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .all()
    )
    assert [m.content for m in rows] == ["Sure — within 14 days."]
    assert rows[0].operator_user_id == operator.id

    chat = db_session.query(Chat).filter(Chat.id == chat.id).one()
    assert chat.operator_state is OperatorState.live
    ticket = db_session.query(EscalationTicket).filter(
        EscalationTicket.id == ticket.id
    ).one()
    assert ticket.status is EscalationStatus.in_progress

    # ...and the same answer went to the visitor by e-mail, so the direct path
    # the Reply-To change took away is restored.
    assert send.call_count == 1
    assert send.call_args.args[0] == "visitor@example.com"
    assert "within 14 days" in send.call_args.args[2]


def test_a_reply_from_a_seatless_sender_is_forwarded_not_refused(
    tenant: TestClient, db_session: Session
) -> None:
    """Acceptance criterion 5. The customer is answered either way."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-fwd@example.com", name="Forward", seated=True
    )
    _colleague(db_session, tenant_id, email="bob@agency.example", seated=False)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="bob@agency.example")
        )

    assert resp.status_code == 200
    assert resp.json()["outcomes"] == [InboundOutcome.forwarded.value]
    assert send.call_args.args[0] == "visitor@example.com"

    db_session.expire_all()
    assert (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .count()
        == 0
    )


def test_a_reply_from_a_stranger_is_forwarded_not_refused(
    tenant: TestClient, db_session: Session
) -> None:
    """An address matching no account at all still answers the customer."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-stranger@example.com", name="Stranger", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="nobody@elsewhere.example")
        )

    assert resp.json()["outcomes"] == [InboundOutcome.forwarded.value]
    assert send.call_args.args[0] == "visitor@example.com"


def test_a_seat_holder_in_another_workspace_is_a_stranger_here(
    tenant: TestClient, db_session: Session
) -> None:
    _token_a, tenant_a = _workspace(
        tenant, db_session, email="a-owner@example.com", name="Alpha Co", seated=True
    )
    _token_b, tenant_b = _workspace(
        tenant, db_session, email="b-owner@example.com", name="Beta Co", seated=True
    )
    _colleague(db_session, tenant_b, email="outsider@b.example", seated=True)
    chat = _chat(db_session, tenant_a)
    ticket = _ticket(db_session, tenant_a, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="outsider@b.example")
        )

    assert resp.json()["outcomes"] == [InboundOutcome.forwarded.value]


def test_the_visitors_own_message_is_not_mailed_back_to_them(
    tenant: TestClient, db_session: Session
) -> None:
    """The loop guard: forwarding this would ping-pong forever."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-loop@example.com", name="Loop", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email") as send:
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="visitor@example.com")
        )

    assert resp.json()["outcomes"] == [InboundOutcome.ignored_loopback.value]
    send.assert_not_called()


def test_a_mismatched_in_reply_to_is_recorded_not_refused(
    tenant: TestClient, db_session: Session
) -> None:
    """The threading check corroborates; it never gates.

    Mail clients rewrite and drop these headers, and a forwarded thread loses
    them entirely — refusing on a mismatch would discard real answers.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-thread@example.com", name="Thread", seated=True
    )
    _colleague(db_session, tenant_id, email="ann@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(
        to=address, sender="ann@agency.example", in_reply_to="<somebody-elses@id>"
    )
    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(tenant, payload)

    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]


def test_a_failed_forward_after_ingestion_keeps_the_message(
    tenant: TestClient, db_session: Session
) -> None:
    """The answer is in the thread; a mail failure must not undo it or retry."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-failsend@example.com", name="FailSend", seated=True
    )
    _colleague(db_session, tenant_id, email="ann@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value=None):
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="ann@agency.example")
        )

    assert resp.status_code == 200
    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]
    db_session.expire_all()
    assert (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .count()
        == 1
    )


def test_a_failed_forward_with_nothing_ingested_asks_for_a_retry(
    tenant: TestClient, db_session: Session
) -> None:
    """Nothing landed anywhere, so a re-delivery cannot duplicate anything."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-retry@example.com", name="Retry", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value=None):
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="nobody@elsewhere.example")
        )

    assert resp.status_code == 503


def test_a_closed_request_is_forwarded_rather_than_reopened(
    tenant: TestClient, db_session: Session
) -> None:
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-closed@example.com", name="Closed", seated=True
    )
    _colleague(db_session, tenant_id, email="ann@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    ticket.status = EscalationStatus.auto_closed
    db_session.add(ticket)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="ann@agency.example")
        )

    assert resp.json()["outcomes"] == [InboundOutcome.forwarded.value]
    assert send.call_args.args[0] == "visitor@example.com"
    db_session.expire_all()
    assert (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .count()
        == 0
    )


def test_an_empty_body_is_dropped_quietly(
    tenant: TestClient, db_session: Session
) -> None:
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-empty@example.com", name="Empty", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(
        to=address, sender="ann@agency.example", extracted="", raw_text="   "
    )
    with patch("backend.escalation.service.send_email") as send:
        resp = _post_inbound(tenant, payload)

    assert resp.json()["outcomes"] == [InboundOutcome.ignored_empty.value]
    send.assert_not_called()


def test_the_signature_reaches_the_mailbox_but_not_the_chat_bubble(
    tenant: TestClient, db_session: Session
) -> None:
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-sig@example.com", name="Sig", seated=True
    )
    _colleague(db_session, tenant_id, email="ann@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(
        to=address, sender="ann@agency.example", signature="--\nAnn, Support"
    )
    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        _post_inbound(tenant, payload)

    assert "Ann, Support" in send.call_args.args[2]
    db_session.expire_all()
    row = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .one()
    )
    assert "Ann, Support" not in row.content


# --------------------------------------------------------------------------
# Direct unit coverage of the seat gate on the way in
# --------------------------------------------------------------------------


def test_handle_inbound_reply_reads_the_seat_not_the_role(
    tenant: TestClient, db_session: Session
) -> None:
    """An owner who never took a seat cannot write into the transcript.

    The role is what somebody may administer; the seat is what they may
    operate. This is the distinction the lane is sold on, so it gets its own
    test rather than being implied by the forwarding case above.
    """
    token, tenant_id = _workspace(
        tenant, db_session, email="unseated-owner@example.com", name="Unseated", seated=True
    )
    owner = (
        db_session.query(User)
        .filter(User.email == "unseated-owner@example.com")
        .one()
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    escalation_reply_to(ticket, db_session)
    db_session.commit()

    [reply] = parse_brevo_payload(
        _brevo_item(
            to=reply_address(ticket.reply_token), sender="unseated-owner@example.com"
        )
    )
    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        seated_result = handle_inbound_reply(reply, db_session)
    assert seated_result.outcome is InboundOutcome.ingested

    # Same person, seat released: the same reply now takes the free path.
    owner.seat_granted_at = None
    db_session.add(owner)
    db_session.commit()
    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        unseated_result = handle_inbound_reply(reply, db_session)
    assert unseated_result.outcome is InboundOutcome.forwarded
    assert token  # the workspace token is unused here; kept for readability
