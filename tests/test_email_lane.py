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
from datetime import timedelta
from unittest import mock
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
    REVOKED_TOKEN_GRACE,
    escalation_reply_to,
    reply_address,
    token_from_recipients,
)
from backend.escalation.service import (
    _notify_tenant_new_ticket,
    stage_ticket_resolved,
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


def test_the_delivered_to_list_is_read_as_well_as_the_to_header() -> None:
    """Brevo's ``Recipients`` carries our plus-address when ``To`` does not.

    An operator whose client put the reply address somewhere ``To`` never
    shows it — a Bcc, a list expansion — would otherwise look like a reply
    addressed to nothing.
    """
    [reply] = parse_brevo_payload(
        {
            "items": [
                {
                    "From": {"Address": "ann@agency.example"},
                    "To": [{"Address": "team@example.com"}],
                    "Recipients": [f"reply+hidden@{_DOMAIN}"],
                    "ExtractedMarkdownMessage": "Done.",
                }
            ]
        }
    )
    assert reply.token == "hidden"


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


def test_a_reply_to_a_just_resolved_request_still_reaches_the_visitor(
    tenant: TestClient, db_session: Session
) -> None:
    """Revocation closes the conversation, not the answer.

    Tickets resolve on their own — the sweeper closes stale ones — so an
    operator answering a notification they read this morning routinely writes
    into a request that has since closed. Erasing the token made that reply
    unattributable to any ticket, which left no visitor to forward it to, and
    it was dropped without a word to the person who wrote it.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="revoke@example.com", name="Revoke", seated=True
    )
    ticket = _ticket(db_session, tenant_id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()
    token = ticket.reply_token
    assert token and token in address

    stage_ticket_resolved(db_session, ticket, "done")
    db_session.commit()

    with mock.patch(
        "backend.escalation.service._send_email_off_loop", return_value="mid"
    ) as send:
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="ann@agency.example")
        )

    assert resp.status_code == 200
    # Forwarded, never ingested: the request is closed, so the reply must not
    # be written into the conversation as if a human had picked it back up.
    assert resp.json()["outcomes"] == ["forwarded"]
    assert send.call_count == 1


def test_a_token_revoked_long_ago_addresses_nothing(
    tenant: TestClient, db_session: Session
) -> None:
    """The grace window ends, and with it the token.

    A notification that leaked must not stay a way to mail the visitor for
    ever, so the address stops resolving once the window has passed — and the
    refusal looks like every other refusal.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="stale@example.com", name="Stale", seated=True
    )
    ticket = _ticket(db_session, tenant_id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    stage_ticket_resolved(db_session, ticket, "done")
    db_session.commit()
    ticket = db_session.get(EscalationTicket, ticket.id)
    ticket.reply_token_revoked_at = _utcnow() - REVOKED_TOKEN_GRACE - timedelta(hours=1)
    db_session.commit()

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


def test_an_html_only_reply_is_not_lost(
    tenant: TestClient, db_session: Session
) -> None:
    """A client that sent HTML and nothing else still gets its answer through.

    Brevo usually hands us extracted markdown, and a plain-text alternative
    usually sits behind it. When neither does, the body used to come out
    empty — ``ignored_empty``, a real answer discarded in silence.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-html@example.com", name="Html", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-html@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number="ESC-9100")
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(
        to=address, sender="ann-html@agency.example", extracted=None, raw_text=""
    )
    payload["items"][0]["RawHtmlBody"] = (
        "<html><body><p>Within 14 days.</p>"
        "<p>Ask billing if it is late &amp; unpaid.</p></body></html>"
    )

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(tenant, payload)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]

    db_session.expire_all()
    written = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .one()
    )
    assert "Within 14 days." in written.content
    # Entities decoded, tags gone, the two paragraphs still apart.
    assert "&amp;" not in written.content
    assert "<p>" not in written.content
    assert "Ask billing if it is late & unpaid." in written.content


def test_a_redelivered_message_is_not_written_twice(
    tenant: TestClient, db_session: Session
) -> None:
    """Brevo re-sends the whole body, and the receipt is what makes that safe.

    Without one, a batch retried because one message failed to send would put
    its already-delivered neighbours through again — a second copy of a human's
    reply in the visitor's conversation.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-dup@example.com", name="Dup", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-dup@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number="ESC-9101")
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(to=address, sender="ann-dup@agency.example")
    payload["items"][0]["Uuid"] = ["brevo-msg-1"]

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        first = _post_inbound(tenant, payload)
        second = _post_inbound(tenant, payload)

    assert first.json()["outcomes"] == [InboundOutcome.ingested.value]
    assert second.status_code == 200, second.text
    assert second.json()["outcomes"] == ["already_handled"]

    db_session.expire_all()
    rows = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .all()
    )
    assert len(rows) == 1


def test_a_failed_send_asks_for_the_batch_again_without_losing_it(
    tenant: TestClient, db_session: Session
) -> None:
    """A message that could not be delivered must come back, and only it.

    The old rule suppressed the retry whenever anything in the batch had been
    ingested, so a batch mixing a written reply with a failed send answered 200
    and dropped the failure on the floor.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-mixed@example.com", name="Mixed", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-mixed@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    good_ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number="ESC-9102")
    bad_ticket = _ticket(
        db_session,
        tenant_id,
        number="ESC-9103",
        user_email="other-visitor@example.com",
    )
    good_address = escalation_reply_to(good_ticket, db_session)
    bad_address = escalation_reply_to(bad_ticket, db_session)
    db_session.commit()

    good = _brevo_item(to=good_address, sender="ann-mixed@agency.example")["items"][0]
    good["Uuid"] = ["brevo-good"]
    # No chat on this ticket, so this one can only ever be forwarded.
    bad = _brevo_item(to=bad_address, sender="ann-mixed@agency.example")["items"][0]
    bad["Uuid"] = ["brevo-bad"]

    def _send(to, *args, **kwargs):
        return None if to == "other-visitor@example.com" else "<fwd@brevo>"

    with patch("backend.escalation.service.send_email", side_effect=_send):
        resp = _post_inbound(tenant, {"items": [good, bad]})

    assert resp.status_code == 503, resp.text

    # The redelivery skips what landed and re-attempts only what did not.
    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        retry = _post_inbound(tenant, {"items": [good, bad]})

    assert retry.status_code == 200, retry.text
    assert retry.json()["outcomes"] == [
        "already_handled",
        InboundOutcome.forwarded.value,
    ]

    db_session.expire_all()
    rows = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .all()
    )
    assert len(rows) == 1


def test_the_quoted_original_does_not_follow_the_reply_into_the_chat(
    tenant: TestClient, db_session: Session
) -> None:
    """The HTML fallback must not carry our own notification back to the visitor.

    The plain-text path never had to think about this — Brevo separates the
    reply from what it was replying to. The fallback has no such help, and
    without trimming it put the ticket number, the visitor's own question and
    their contact details into the bubble they read, and mailed the same thing
    back to them.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-quote@example.com", name="Quote", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-quote@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number="ESC-9104")
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(
        to=address, sender="ann-quote@agency.example", extracted=None, raw_text=""
    )
    payload["items"][0]["RawHtmlBody"] = (
        '<div dir="ltr">Refunds take 14 days.</div><br>'
        '<div class="gmail_quote"><div class="gmail_attr">On Mon, Chat9 wrote:</div>'
        "<blockquote><p>New escalation ESC-9104</p>"
        "<p>Visitor asked: my card is 4111 1111 1111 1111</p></blockquote></div>"
    )

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(tenant, payload)

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    written = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .one()
    )
    assert written.content == "Refunds take 14 days."
    assert "4111" not in written.content
    assert "ESC-9104" not in written.content
    # The same trimming has to hold on the copy mailed to the visitor.
    forwarded_body = send.call_args.args[2]
    assert "4111" not in forwarded_body


def test_a_reply_written_below_the_quote_is_still_delivered(
    tenant: TestClient, db_session: Session
) -> None:
    """Trimming must never turn an answer into an empty message.

    Cutting at the first quote marker suits the overwhelming majority, who type
    above it. For the person who types underneath, a reply with the history
    attached beats ``ignored_empty``.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-below@example.com", name="Below", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-below@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number="ESC-9105")
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(
        to=address, sender="ann-below@agency.example", extracted=None, raw_text=""
    )
    payload["items"][0]["RawHtmlBody"] = (
        "<blockquote><p>New escalation ESC-9105</p></blockquote>"
        "<div>Answering below: refunds take 14 days.</div>"
    )

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(tenant, payload)

    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]
    db_session.expire_all()
    written = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .one()
    )
    assert "refunds take 14 days" in written.content


def test_a_reopened_ticket_advertises_an_address_that_works(
    tenant: TestClient, db_session: Session
) -> None:
    """Re-minting has to lift the revocation it is handing out an address past.

    A ticket closes, is reopened, and a fresh notification goes out carrying
    the same reply address. With yesterday's revocation still stamped, the
    operator answers today's mail and gets a 404 — the very silence the stamp
    was introduced to end.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-reopen@example.com", name="Reopen", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-reopen@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number="ESC-9106")
    escalation_reply_to(ticket, db_session)
    db_session.commit()

    stage_ticket_resolved(db_session, ticket, "done")
    db_session.commit()
    ticket = db_session.get(EscalationTicket, ticket.id)
    assert ticket.reply_token_revoked_at is not None

    # Reopened, and a new notification minted for it.
    ticket.status = EscalationStatus.open
    ticket.resolved_at = None
    db_session.commit()
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    db_session.expire_all()
    ticket = db_session.get(EscalationTicket, ticket.id)
    assert ticket.reply_token_revoked_at is None

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(
            tenant, _brevo_item(to=address, sender="ann-reopen@agency.example")
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]


def test_a_from_header_sent_as_a_string_still_identifies_the_operator(
    tenant: TestClient, db_session: Session
) -> None:
    """``From`` arriving as a full header value must not turn a seat holder into
    a stranger.

    ``token_from_recipients`` parses addresses properly; ``_first_address`` did
    not, for the one shape it went out of its way to handle. Every lookup would
    have missed, and the ingest half of the lane would have quietly become a
    mail relay.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-str@example.com", name="Str", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-str@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number="ESC-9107")
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(to=address, sender="unused@example.com")
    payload["items"][0]["From"] = "Ann Smith <ann-str@agency.example>"

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(tenant, payload)

    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value], resp.text


def test_an_operator_answering_from_the_address_the_visitor_gave_is_not_dropped(
    tenant: TestClient, db_session: Session
) -> None:
    """The commonest false positive in the loopback guard, and the worst-placed.

    The address on a ticket is whatever the visitor typed. A tenant trying
    their own widget types their own support address, then answers from it —
    and an unqualified drop makes the feature look broken on the first contact
    anyone has with it. Holding a seat is what separates a member of this
    workspace from a visitor bouncing a forward.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-self@example.com", name="Self", seated=True
    )
    _colleague(db_session, tenant_id, email="support@theircompany.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(
        db_session,
        tenant_id,
        chat_id=chat.id,
        number="ESC-9108",
        user_email="support@theircompany.example",
    )
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(
            tenant,
            _brevo_item(to=address, sender="support@theircompany.example"),
        )

    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value], resp.text


def test_the_visitor_replying_to_a_forward_is_still_dropped(
    tenant: TestClient, db_session: Session
) -> None:
    """The property the guard exists for has to survive the narrowing.

    A visitor holds no seat, so their own words coming back still stop here
    rather than being mailed to them again.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-ping@example.com", name="Ping", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(
        db_session,
        tenant_id,
        chat_id=chat.id,
        number="ESC-9109",
        user_email="visitor@example.com",
    )
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    resp = _post_inbound(
        tenant, _brevo_item(to=address, sender="visitor@example.com")
    )
    assert resp.json()["outcomes"] == [InboundOutcome.ignored_loopback.value]
