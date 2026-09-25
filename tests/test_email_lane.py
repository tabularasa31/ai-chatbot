"""The inbound e-mail lane: an operator answering from their mailbox.

The behaviours worth protecting, in the order they matter:

* a workspace with no seat sees no change at all — same ``Reply-To``, same
  straight-to-the-visitor path;
* a seat holder's reply lands in the chat thread *and* in the visitor's inbox,
  and mutes the bot exactly as the operator API's ``take`` does — releasing
  the chat hands it back;
* a reply from anybody else is forwarded to the visitor and never refused;
* the visitor's replies reach the operator while the chat is live;
* the endpoint refuses a missing path secret and a token matching no ticket.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

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
    ticket_for_token,
    token_from_recipients,
)
from backend.escalation.service import (
    _notify_tenant_new_ticket,
    note_repeat_human_request,
    stage_ticket_resolved,
)
from backend.models import (
    Chat,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
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
from backend.operator.unread_reply import mail_unread_operator_replies
from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import (
    get_default_bot_public_id,
    post_chat_message,
    register_and_verify_user,
    set_client_openai_key,
)

_SECRET = "inbound-secret-for-tests"
_DOMAIN = "reply.getchat9.live"


@pytest.fixture(autouse=True)
def _wire_the_lane(monkeypatch: pytest.MonkeyPatch):
    """Configure the lane for every test in this module.

    Without the secret the lane is not wired at all — that state has its own
    case rather than being the default here.
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


def _workspace_with_key(
    client: TestClient, db: Session, *, email: str, name: str, seated: bool
) -> tuple[str, uuid.UUID, str]:
    """Same as ``_workspace``, plus the default bot's public id for a
    ``/widget/chat`` call."""
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
    body = resp.json()
    bot_public_id = get_default_bot_public_id(client, token)
    return token, uuid.UUID(body["id"]), bot_public_id


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


def _seed_knowledge(db: Session, tenant_id: uuid.UUID) -> None:
    """One indexed chunk, so a bot turn has something to answer from."""
    doc = Document(
        tenant_id=tenant_id,
        filename="handbook.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Refunds are issued within 14 days.",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    db.add(
        Embedding(
            document_id=doc.id,
            chunk_text="Refunds are issued within 14 days.",
            vector=None,
            metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
        )
    )
    db.commit()


def _arm_openai(mock_openai_client: Mock, answer: str = "Within 14 days.") -> None:
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 1536)
    ]
    mock_openai_client.chat.completions.create.side_effect = (
        _chat_completion_side_effect(answer, total_tokens=7)
    )


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


@pytest.mark.parametrize(
    "seated,wired,expect_token",
    [
        pytest.param(False, True, False, id="seatless_workspace_keeps_visitor_address"),
        pytest.param(True, True, True, id="seated_workspace_gets_token_address"),
        pytest.param(True, False, False, id="unwired_lane_keeps_visitor_address"),
    ],
)
def test_notification_reply_to_by_seat_status(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    seated: bool,
    wired: bool,
    expect_token: bool,
) -> None:
    """Acceptance criterion 1: a tenant with no seat, or no wired lane, sees no
    change at all — same address the visitor already gave.
    """
    if not wired:
        monkeypatch.setattr(settings, "inbound_email_secret", None)
    _token, tenant_id = _workspace(
        tenant, db_session, email=f"notify-{seated}-{wired}@example.com",
        name="Notify Co", seated=seated,
    )
    ticket = _ticket(db_session, tenant_id)

    if wired:
        workspace = db_session.query(Tenant).filter(Tenant.id == tenant_id).one()
        with patch("backend.escalation.service.send_email", return_value="<id@brevo>") as send:
            assert _notify_tenant_new_ticket(workspace, ticket, db_session) is True
        reply_to = send.call_args.kwargs["reply_to"]
    else:
        reply_to = escalation_reply_to(ticket, db_session)

    db_session.refresh(ticket)
    if expect_token:
        assert ticket.reply_token
        assert reply_to == reply_address(ticket.reply_token)
    else:
        assert ticket.reply_token is None
        assert reply_to == "visitor@example.com"


def test_reply_to_token_is_stable_across_renotify(
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


def test_token_from_recipients_parses_the_plus_address() -> None:
    assert token_from_recipients([f"reply+abc123@{_DOMAIN}"]) == "abc123"
    assert token_from_recipients([f"Ann <reply+abc123@{_DOMAIN}>"]) == "abc123"
    # Another domain is somebody else's mail, not a malformed token.
    assert token_from_recipients(["reply+abc123@example.com"]) is None
    assert token_from_recipients([f"support@{_DOMAIN}"]) is None
    assert token_from_recipients([]) is None


def test_a_token_survives_case_folding_from_mint_to_reply(
    tenant: TestClient, db_session: Session
) -> None:
    """Brevo lower-cases ``Reply-To`` on send, so what comes back is not
    byte-identical to what was minted. Pinned against the real ESC-0659 miss:
    a mixed-case token in the row, a lower-cased one in the reply, 404.

    Freshly minted tokens carry no case at all, so this is a legacy-token
    concern rather than something new tokens can reintroduce.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="fold@example.com", name="Fold", seated=True
    )
    ticket = _ticket(db_session, tenant_id)
    ticket.reply_token = "MiXeD-Case_Legacy_Token"
    db_session.commit()

    assert ticket_for_token("mixed-case_legacy_token", db_session) is ticket
    assert token_from_recipients([f"reply+MiXeD-Case@{_DOMAIN}"]) == "mixed-case"

    fresh = _ticket(db_session, tenant_id, number="ESC-9002")
    address = escalation_reply_to(fresh, db_session)
    token = fresh.reply_token
    assert token and token == token.lower() and token in address
    assert len(f"reply+{token}") <= 64  # RFC 5321 local-part limit


@pytest.mark.parametrize(
    "case_id,item_kwargs,expected_text,expected_token",
    [
        pytest.param(
            "extracted_preferred",
            {},
            "Sure — within 14 days.",
            None,
            id="extracted_preferred",
        ),
        pytest.param(
            "raw_fallback",
            {"extracted": None, "raw_text": "Plain body"},
            "Plain body",
            None,
            id="raw_text_fallback",
        ),
    ],
)
def test_parse_brevo_payload_body_selection(
    case_id: str, item_kwargs: dict, expected_text: str, expected_token: str | None
) -> None:
    """No quote-stripping heuristics on the extracted path: Brevo already did
    the separation. The raw text is only the fallback when it did not.
    """
    [reply] = parse_brevo_payload(
        _brevo_item(to=f"reply+tok@{_DOMAIN}", sender="ann@agency.example", **item_kwargs)
    )
    assert reply.text == expected_text
    if case_id == "extracted_preferred":
        assert "On Mon, we wrote:" not in reply.text


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


def test_inbound_refusals(tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Four distinct ways in, all refused with a 404:

    * a path secret that does not match ours;
    * a lane never configured with a secret at all;
    * a token that matches no ticket;
    * a token revoked long enough ago that its grace window has passed.
    """
    item = _brevo_item(to=f"reply+nosuchtoken@{_DOMAIN}", sender="ann@agency.example")

    assert _post_inbound(tenant, item, secret="not-the-secret").status_code == 404

    monkeypatch.setattr(settings, "inbound_email_secret", None)
    assert _post_inbound(tenant, item).status_code == 404
    monkeypatch.setattr(settings, "inbound_email_secret", _SECRET)

    assert _post_inbound(tenant, item).status_code == 404

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

    assert _post_inbound(
        tenant, _brevo_item(to=address, sender="ann@agency.example")
    ).status_code == 404


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

    with patch("backend.escalation.service.send_email", return_value="mid") as send:
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="ann@agency.example"))

    assert resp.status_code == 200
    # Forwarded, never ingested: the request is closed, so the reply must not
    # be written into the conversation as if a human had picked it back up.
    assert resp.json()["outcomes"] == ["forwarded"]
    assert send.call_count == 1


# --------------------------------------------------------------------------
# Inbound: attribution, and the operator take/answer/release journey
# --------------------------------------------------------------------------


def test_seat_holders_reply_take_answer_release_journey(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Acceptance criteria 2 and 3, plus the handoff they produce, in one pass:

    * a seat holder's e-mail reply lands in the thread and reaches the
      visitor by e-mail too — the direct path the Reply-To change took away;
    * it leaves no forward mark, because it was ingested, not forwarded;
    * ingestion mutes the bot exactly as ``/operator/.../take`` does;
    * the visitor's already-mailed reply means the unread-reply job has
      nothing left to send;
    * ``/operator/.../release`` hands the chat back and the bot resumes;
    * once released, the seat holder's own seat — not their role — is what
      decided the outcome: releasing it turns the same reply into a forward.
    """
    token, tenant_id, bot_public_id = _workspace_with_key(
        tenant, db_session, email="owner-ingest@example.com", name="Ingest", seated=True
    )
    operator = _colleague(db_session, tenant_id, email="ann@agency.example", seated=True)
    _seed_knowledge(db_session, tenant_id)
    _arm_openai(mock_openai_client, answer="Refunds take 14 days.")
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="Ann@Agency.example"))

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
    ticket = db_session.get(EscalationTicket, ticket.id)
    assert ticket.status is EscalationStatus.in_progress
    # Ingested, not forwarded: no mark left behind.
    assert ticket.forwarded_reply_at is None
    assert ticket.forwarded_reply_from is None

    # ...and the same answer went to the visitor by e-mail.
    assert send.call_count == 1
    assert send.call_args.args[0] == "visitor@example.com"
    assert "within 14 days" in send.call_args.args[2]

    # The visitor already holds this reply in their mailbox, so the unread-
    # reply job that the ingest scheduled must find nothing left to send.
    assert chat.unread_reply_mailed_message_id == rows[0].id
    with patch("backend.operator.unread_reply.send_email") as again:
        outcome = mail_unread_operator_replies(db_session, chat_id=chat.id, message_id=rows[0].id)
    assert outcome == "nothing_unread"
    again.assert_not_called()

    # The bot is muted while the chat is live, same as after a `take`.
    muted = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="any update?", session_id=str(chat.session_id)
    )
    assert muted.status_code == 200, muted.text
    assert muted.json()["text"] == ""

    # Released through the real operator API, the bot answers again.
    released = tenant.post(
        f"/operator/chats/{chat.id}/release",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert released.status_code == 200, released.text
    resumed = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="any update?", session_id=str(chat.session_id)
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["text"] != ""

    # Same person, seat released: the same reply now takes the free path.
    operator.seat_granted_at = None
    db_session.add(operator)
    db_session.commit()
    with patch("backend.escalation.service.send_email", return_value="<fwd2@brevo>"):
        [reply] = parse_brevo_payload(
            _brevo_item(to=reply_address(ticket.reply_token), sender="ann@agency.example")
        )
        unseated_result = handle_inbound_reply(reply, db_session)
    assert unseated_result.outcome is InboundOutcome.forwarded

    # The owner role grants nothing on its own: without a seat the owner is
    # forwarded like anyone else.
    owner = db_session.query(User).filter(User.email == "owner-ingest@example.com").one()
    owner.seat_granted_at = None
    db_session.add(owner)
    db_session.commit()
    with patch("backend.escalation.service.send_email", return_value="<fwd3@brevo>"):
        [reply] = parse_brevo_payload(
            _brevo_item(to=reply_address(ticket.reply_token), sender="owner-ingest@example.com")
        )
        owner_result = handle_inbound_reply(reply, db_session)
    assert owner_result.outcome is InboundOutcome.forwarded


def test_a_seat_holders_reply_the_forward_lost_is_mailed_by_the_job(
    tenant: TestClient, db_session: Session
) -> None:
    """A failed forward is not the end of it: the grace-period job retries by mail."""
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-lost@example.com", name="Lost", seated=True
    )
    _colleague(db_session, tenant_id, email="lee@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value=None):
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="lee@agency.example"))
    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]

    db_session.expire_all()
    reply = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .one()
    )
    chat = db_session.query(Chat).filter(Chat.id == chat.id).one()
    assert chat.unread_reply_mailed_message_id is None

    with patch("backend.operator.unread_reply.send_email", return_value="<id>") as send:
        outcome = mail_unread_operator_replies(db_session, chat_id=chat.id, message_id=reply.id)
    assert outcome == "sent"
    assert send.call_args.args[0] == "visitor@example.com"


@pytest.mark.parametrize(
    "scenario",
    ["seatless_colleague", "stranger", "cross_tenant_seat_holder"],
)
def test_non_seat_reply_is_forwarded_not_refused(
    tenant: TestClient, db_session: Session, scenario: str
) -> None:
    """Acceptance criterion 5: the customer is answered either way, whoever
    replies — a colleague without a seat, a total stranger, or a seat holder
    who just happens to hold it on somebody else's workspace.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email=f"owner-{scenario}@example.com", name="Fwd", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)

    if scenario == "seatless_colleague":
        _colleague(db_session, tenant_id, email="bob@agency.example", seated=False)
        sender = "bob@agency.example"
    elif scenario == "stranger":
        sender = "nobody@elsewhere.example"
    else:
        _token_b, tenant_b = _workspace(
            tenant, db_session, email="b-owner@example.com", name="Beta Co", seated=True
        )
        _colleague(db_session, tenant_b, email="outsider@b.example", seated=True)
        sender = "outsider@b.example"

    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(tenant, _brevo_item(to=address, sender=sender))

    assert resp.status_code == 200
    assert resp.json()["outcomes"] == [InboundOutcome.forwarded.value]
    if scenario != "cross_tenant_seat_holder":
        assert send.call_args.args[0] == "visitor@example.com"

    db_session.expire_all()
    assert (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .count()
        == 0
    )


def test_a_forwarded_reply_leaves_its_mark_on_the_ticket_and_in_the_inbox(
    tenant: TestClient, db_session: Session
) -> None:
    """The answer went out by mail, outside the product. Until this stamp the
    inbox showed the request as never answered and a colleague answered it
    again. Sender and time are kept; the body is not — the reply is not a
    message of this conversation and must not be stored as one. The queue
    treats it as an answer all the same: the visitor is no longer waiting.
    """
    token, tenant_id = _workspace(
        tenant, db_session, email="owner-mark@example.com", name="Mark", seated=True
    )
    chat = _chat(db_session, tenant_id)
    db_session.add(Message(chat_id=chat.id, role=MessageRole.user, content="Help me"))
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="alias@agency.example"))
    assert resp.json()["outcomes"] == [InboundOutcome.forwarded.value]

    db_session.expire_all()
    assert ticket.forwarded_reply_from == "alias@agency.example"
    assert ticket.forwarded_reply_at is not None
    assert ticket.status is EscalationStatus.open
    assert [m.role for m in db_session.query(Message).filter(Message.chat_id == chat.id)] == [
        MessageRole.user
    ]

    auth = {"Authorization": f"Bearer {token}"}
    queue = tenant.get("/operator/inbox", headers=auth).json()
    assert queue["items"] == []
    assert queue["waiting_count"] == 0
    [row] = tenant.get("/operator/inbox?scope=all", headers=auth).json()["items"]
    assert row["handoff_state"] == "bot"
    assert row["waiting_since"] is None
    assert row["ticket"]["forwarded_reply_from"] == "alias@agency.example"
    assert row["ticket"]["forwarded_reply_at"]
    thread = tenant.get(f"/operator/sessions/{chat.session_id}", headers=auth).json()
    assert thread["handoff_state"] == "bot"
    assert thread["ticket"]["forwarded_reply_from"] == "alias@agency.example"
    assert [m["role"] for m in thread["messages"]] == ["user"]


def test_a_forwarded_reply_answers_the_request_so_asking_again_re_queues(
    tenant: TestClient, db_session: Session
) -> None:
    """The visitor got their answer by mail; asking for a human again is a new
    request and starts a new wait, exactly as after an answer in the thread.
    Before the forward the repeat changes nothing — the visitor still waits
    from the first request. After it, the mark hides again, since it counts
    against the request it answered and not the next one.
    """
    token, tenant_id = _workspace(
        tenant, db_session, email="owner-requeue@example.com", name="Requeue", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()
    assert note_repeat_human_request(ticket, db_session) is False

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
        _post_inbound(tenant, _brevo_item(to=address, sender="alias@agency.example"))

    db_session.expire_all()
    assert note_repeat_human_request(ticket, db_session) is True
    db_session.commit()

    auth = {"Authorization": f"Bearer {token}"}
    queue = tenant.get("/operator/inbox", headers=auth).json()
    [row] = queue["items"]
    assert row["handoff_state"] == "waiting"
    assert row["ticket"]["forwarded_reply_at"] is None
    assert queue["waiting_count"] == 1


def test_forwarded_reply_response_ms_metric_and_stale_clock_guard(
    tenant: TestClient, db_session: Session
) -> None:
    """The visitor waited exactly until this mail, so the wait is recorded on
    the forward's own event, never on ``first_response_ms``: the product
    metric measures the product, and this answer happened outside it.

    A second forward whose stamp did not land must not report the first
    forward's wait: the rolled-back session reloads yesterday's stamp, and a
    stale number is worse than none. The forward itself still succeeds.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email="owner-clock@example.com", name="Clock", seated=True
    )
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id)
    address = escalation_reply_to(ticket, db_session)
    ticket.created_at = _utcnow() - timedelta(minutes=10)
    ticket.requested_again_at = _utcnow() - timedelta(minutes=3)
    db_session.commit()

    with (
        patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"),
        patch("backend.email.inbound.capture_event") as captured,
    ):
        _post_inbound(tenant, _brevo_item(to=address, sender="alias@agency.example"))

    db_session.expire_all()
    [forwarded] = [
        c for c in captured.call_args_list if c.args[0] == "email_lane.reply_forwarded"
    ]
    expected = int(
        (ticket.forwarded_reply_at - ticket.requested_again_at).total_seconds() * 1000
    )
    assert forwarded.kwargs["properties"]["response_ms"] == expected
    assert timedelta(minutes=3) <= timedelta(milliseconds=expected) < timedelta(minutes=4)
    first_stamp = ticket.forwarded_reply_at

    with (
        patch("backend.escalation.service.send_email", return_value="<fwd2@brevo>"),
        patch("backend.email.inbound._utcnow", side_effect=RuntimeError("clock down")),
        patch("backend.email.inbound.capture_event") as captured2,
    ):
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="alias@agency.example"))
    assert resp.json()["outcomes"] == [InboundOutcome.forwarded.value]

    db_session.expire_all()
    assert ticket.forwarded_reply_at == first_stamp
    [forwarded2] = [
        c for c in captured2.call_args_list if c.args[0] == "email_lane.reply_forwarded"
    ]
    assert forwarded2.kwargs["properties"]["response_ms"] is None


@pytest.mark.parametrize(
    "scenario,expected_outcome",
    [
        pytest.param("visitor_replies_direct", InboundOutcome.ignored_loopback, id="visitor_direct"),
        pytest.param("visitor_replies_to_forward", InboundOutcome.ignored_loopback, id="visitor_to_forward"),
        pytest.param("seat_holder_shares_visitor_address", InboundOutcome.ingested, id="seat_holder_same_address"),
    ],
)
def test_loopback_guard_keys_on_seat_not_address(
    tenant: TestClient, db_session: Session, scenario: str, expected_outcome: InboundOutcome
) -> None:
    """The loop guard drops the visitor's own words coming back — forwarding
    those would ping-pong forever, whether they arrive straight or bounced off
    a forward. But holding a seat is what separates a member of this
    workspace from a visitor: a tenant testing their own widget from their own
    support address must still get through.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email=f"owner-{scenario}@example.com", name="Loop", seated=True
    )
    chat = _chat(db_session, tenant_id)
    visitor_email = "support@theircompany.example" if scenario == "seat_holder_shares_visitor_address" else "visitor@example.com"
    if scenario == "seat_holder_shares_visitor_address":
        _colleague(db_session, tenant_id, email=visitor_email, seated=True)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, user_email=visitor_email)
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    if scenario == "visitor_replies_to_forward":
        with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>"):
            _post_inbound(tenant, _brevo_item(to=address, sender="alias@agency.example"))

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(tenant, _brevo_item(to=address, sender=visitor_email))

    assert resp.json()["outcomes"][-1] == expected_outcome.value
    if expected_outcome is InboundOutcome.ignored_loopback:
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

    payload = _brevo_item(to=address, sender="ann@agency.example", in_reply_to="<somebody-elses@id>")
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
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="ann@agency.example"))

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
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="nobody@elsewhere.example"))

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
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="ann@agency.example"))

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

    payload = _brevo_item(to=address, sender="ann@agency.example", extracted="", raw_text="   ")
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

    payload = _brevo_item(to=address, sender="ann@agency.example", signature="--\nAnn, Support")
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

    payload = _brevo_item(to=address, sender="ann-html@agency.example", extracted=None, raw_text="")
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
        db_session, tenant_id, number="ESC-9103", user_email="other-visitor@example.com"
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
    assert retry.json()["outcomes"] == ["already_handled", InboundOutcome.forwarded.value]

    db_session.expire_all()
    rows = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .all()
    )
    assert len(rows) == 1


@pytest.mark.parametrize(
    "quote_position,expect_dropped",
    [
        pytest.param("above", None, id="quote_above_reply_is_trimmed"),
        pytest.param("below", "refunds take 14 days", id="reply_below_quote_still_delivered"),
    ],
)
def test_html_fallback_quote_trimming(
    tenant: TestClient, db_session: Session, quote_position: str, expect_dropped: str | None
) -> None:
    """The HTML fallback must not carry our own notification back to the
    visitor. The plain-text path never has to think about this — Brevo
    separates the reply from what it was replying to — but without trimming,
    the fallback put the ticket number, the visitor's own question and their
    contact details into the bubble they read, and mailed the same thing back
    to them.

    Cutting at the first quote marker suits the overwhelming majority, who
    type above it. For the person who types underneath, a reply with the
    history attached beats ``ignored_empty``.
    """
    _token, tenant_id = _workspace(
        tenant, db_session, email=f"owner-quote-{quote_position}@example.com", name="Quote", seated=True
    )
    _colleague(db_session, tenant_id, email="ann-quote@agency.example", seated=True)
    chat = _chat(db_session, tenant_id)
    ticket = _ticket(db_session, tenant_id, chat_id=chat.id, number=f"ESC-910{quote_position[0]}")
    address = escalation_reply_to(ticket, db_session)
    db_session.commit()

    payload = _brevo_item(to=address, sender="ann-quote@agency.example", extracted=None, raw_text="")
    if quote_position == "above":
        payload["items"][0]["RawHtmlBody"] = (
            '<div dir="ltr">Refunds take 14 days.</div><br>'
            '<div class="gmail_quote"><div class="gmail_attr">On Mon, Chat9 wrote:</div>'
            f"<blockquote><p>New escalation {ticket.ticket_number}</p>"
            "<p>Visitor asked: my card is 4111 1111 1111 1111</p></blockquote></div>"
        )
    else:
        payload["items"][0]["RawHtmlBody"] = (
            f"<blockquote><p>New escalation {ticket.ticket_number}</p></blockquote>"
            "<div>Answering below: refunds take 14 days.</div>"
        )

    with patch("backend.escalation.service.send_email", return_value="<fwd@brevo>") as send:
        resp = _post_inbound(tenant, payload)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcomes"] == [InboundOutcome.ingested.value]
    db_session.expire_all()
    written = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.operator)
        .one()
    )
    if quote_position == "above":
        assert written.content == "Refunds take 14 days."
        assert "4111" not in written.content
        assert ticket.ticket_number not in written.content
        # The same trimming has to hold on the copy mailed to the visitor.
        assert "4111" not in send.call_args.args[2]
    else:
        assert expect_dropped in written.content.lower()


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
        resp = _post_inbound(tenant, _brevo_item(to=address, sender="ann-reopen@agency.example"))

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
