"""The operator console: the queue, one thread, the seat gate, and "resolved".

The console is the first place a person sees what is waiting and says it is
dealt with. What these tests pin down is what a seat holder can do that a
plain member cannot, that "resolved" closes the request everywhere (ticket,
reply token, the bot's silence), and that nothing here reaches across
tenants — a foreign session or chat is a 404 on every route.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth.roles import ROLE_OPERATOR
from backend.auth.service import create_token_for_user
from backend.email.reply_lane import mint_reply_token
from backend.escalation.service import _notify_tenant_new_ticket
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


class _Workspace:
    def __init__(self, token: str, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.token = token
        self.tenant_id = tenant_id
        self.user_id = user_id

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _workspace(
    client: TestClient, db: Session, *, email: str, name: str, seated: bool = True
) -> _Workspace:
    token = register_and_verify_user(client, db, email=email)
    resp = client.post("/tenants", headers={"Authorization": f"Bearer {token}"}, json={"name": name})
    assert resp.status_code == 201, resp.text
    set_client_openai_key(client, token)
    if seated:
        seat = client.put("/tenants/members/me/seat", headers={"Authorization": f"Bearer {token}"})
        assert seat.status_code == 200, seat.text
    user = db.query(User).filter(User.email == email).one()
    return _Workspace(token, uuid.UUID(resp.json()["id"]), user.id)


def _colleague(db: Session, tenant_id: uuid.UUID, *, email: str) -> User:
    user = User(
        email=email,
        password_hash="x",
        role=ROLE_OPERATOR,
        is_verified=True,
        tenant_id=tenant_id,
        seat_granted_at=_utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _chat(db: Session, tenant_id: uuid.UUID, **kwargs) -> Chat:
    chat = Chat(tenant_id=tenant_id, session_id=kwargs.pop("session_id", uuid.uuid4()), **kwargs)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _say(db: Session, chat: Chat, role: MessageRole, content: str, **kwargs) -> Message:
    message = Message(chat_id=chat.id, role=role, content=content, **kwargs)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


_ticket_seq = iter(range(1000, 100000))


def _ticket(
    db: Session,
    chat: Chat,
    *,
    status: EscalationStatus = EscalationStatus.open,
    created_ago: timedelta = timedelta(0),
    user_email: str | None = "visitor@example.com",
) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=chat.tenant_id,
        ticket_number=f"ESC-{next(_ticket_seq)}",
        primary_question="How do refunds work?",
        trigger=EscalationTrigger.user_request,
        status=status,
        chat_id=chat.id,
        session_id=chat.session_id,
        user_email=user_email,
        user_name="Ivan",
        created_at=_utcnow() - created_ago,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


def test_the_queue_lists_what_needs_a_human_longest_wait_first(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="queue@example.com", name="Queue Co")
    quiet = _chat(db_session, ws.tenant_id)
    _say(db_session, quiet, MessageRole.user, "just browsing")

    recent = _chat(db_session, ws.tenant_id)
    _ticket(db_session, recent, created_ago=timedelta(minutes=2))
    _say(db_session, recent, MessageRole.user, "I need a person")

    old = _chat(db_session, ws.tenant_id)
    _ticket(db_session, old, created_ago=timedelta(hours=1))
    _say(db_session, old, MessageRole.user, "still waiting")

    live = _chat(
        db_session,
        ws.tenant_id,
        operator_state=OperatorState.live,
        assigned_operator_id=ws.user_id,
        operator_joined_at=_utcnow(),
    )
    _ticket(db_session, live, status=EscalationStatus.in_progress)
    _say(db_session, live, MessageRole.operator, "On it", operator_user_id=ws.user_id)

    resp = tenant.get("/operator/inbox", headers=ws.auth)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["chat_id"] for r in body["items"]] == [str(old.id), str(recent.id), str(live.id)]
    assert [r["handoff_state"] for r in body["items"]] == ["waiting", "waiting", "live"]
    assert body["waiting_count"] == 2
    assert body["attention_count"] == 3

    held = body["items"][2]
    assert held["assigned_operator_email"] == "queue@example.com"
    assert held["last_message_preview"] == "On it"
    assert held["last_message_role"] == "operator"
    assert held["ticket"]["status"] == "in_progress"
    waiting = body["items"][0]
    assert waiting["assigned_operator_email"] is None
    assert waiting["waiting_since"] is not None
    assert waiting["visitor_email"] == "visitor@example.com"
    assert waiting["visitor_name"] == "Ivan"


def test_scope_all_includes_conversations_the_bot_is_handling(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="all@example.com", name="All Co")
    quiet = _chat(db_session, ws.tenant_id, user_context={"email": "q@example.com", "name": "Q"})
    _say(db_session, quiet, MessageRole.user, "just browsing")
    _say(db_session, quiet, MessageRole.assistant, "Sure, ask away.")
    waiting = _chat(db_session, ws.tenant_id)
    _ticket(db_session, waiting)

    attention = tenant.get("/operator/inbox", headers=ws.auth).json()
    everything = tenant.get("/operator/inbox?scope=all", headers=ws.auth).json()

    assert [r["chat_id"] for r in attention["items"]] == [str(waiting.id)]
    assert {r["chat_id"] for r in everything["items"]} == {str(quiet.id), str(waiting.id)}
    row = next(r for r in everything["items"] if r["chat_id"] == str(quiet.id))
    assert row["handoff_state"] == "bot"
    assert row["ticket"] is None
    assert row["message_count"] == 2
    assert row["visitor_email"] == "q@example.com"


def test_a_rotated_session_is_one_row_pointing_at_its_newest_chat(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="rot@example.com", name="Rot Co")
    session_id = uuid.uuid4()
    first = _chat(db_session, ws.tenant_id, session_id=session_id, ended_at=_utcnow())
    _say(db_session, first, MessageRole.user, "yesterday")
    second = _chat(db_session, ws.tenant_id, session_id=session_id)
    _say(db_session, second, MessageRole.user, "today")

    rows = tenant.get("/operator/inbox?scope=all", headers=ws.auth).json()["items"]

    assert len(rows) == 1
    assert rows[0]["session_id"] == str(session_id)
    assert rows[0]["chat_id"] == str(second.id)
    assert rows[0]["last_message_preview"] == "today"


def test_the_summary_is_the_badge(tenant: TestClient, db_session: Session) -> None:
    ws = _workspace(tenant, db_session, email="badge@example.com", name="Badge Co")
    _ticket(db_session, _chat(db_session, ws.tenant_id))
    _ticket(db_session, _chat(db_session, ws.tenant_id))
    taken = _chat(
        db_session,
        ws.tenant_id,
        operator_state=OperatorState.live,
        assigned_operator_id=ws.user_id,
        operator_joined_at=_utcnow(),
    )
    _ticket(db_session, taken, status=EscalationStatus.in_progress)

    resp = tenant.get("/operator/inbox/summary", headers=ws.auth)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"waiting_count": 2, "attention_count": 3}


# --------------------------------------------------------------------------
# The thread
# --------------------------------------------------------------------------


def test_the_thread_spans_the_session_and_signs_operator_turns(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="thread@example.com", name="Thread Co")
    colleague = _colleague(db_session, ws.tenant_id, email="ann@thread.example")
    session_id = uuid.uuid4()
    first = _chat(db_session, ws.tenant_id, session_id=session_id, ended_at=_utcnow())
    _say(db_session, first, MessageRole.user, "hello")
    _say(db_session, first, MessageRole.assistant, "hi")
    second = _chat(
        db_session,
        ws.tenant_id,
        session_id=session_id,
        operator_state=OperatorState.live,
        assigned_operator_id=colleague.id,
        operator_joined_at=_utcnow(),
    )
    ticket = _ticket(db_session, second, status=EscalationStatus.in_progress)
    _say(db_session, second, MessageRole.user, "I need a person")
    _say(db_session, second, MessageRole.operator, "Ann here", operator_user_id=colleague.id)
    _say(db_session, second, MessageRole.operator, "Bob was here", operator_label="bob@gone.example")
    _say(db_session, second, MessageRole.operator, "from an unknown mailbox")

    resp = tenant.get(f"/operator/sessions/{session_id}", headers=ws.auth)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handoff_state"] == "live"
    assert body["chat"]["chat_id"] == str(second.id)
    assert body["chat"]["assigned_operator_email"] == "ann@thread.example"
    assert body["ticket"]["ticket_number"] == ticket.ticket_number
    assert body["visitor_email"] == "visitor@example.com"
    assert [m["chat_id"] for m in body["messages"]] == [str(first.id)] * 2 + [str(second.id)] * 4
    assert [m["role"] for m in body["messages"]] == [
        "user", "assistant", "user", "operator", "operator", "operator"
    ]
    assert [m["author_label"] for m in body["messages"][3:]] == [
        "ann@thread.example",
        "bob@gone.example",
        None,
    ]


def test_a_reply_by_email_and_a_reply_from_the_console_look_the_same(
    tenant: TestClient, db_session: Session
) -> None:
    from backend.operator.service import OperatorActor, OperatorChannel, ingest_from_operator

    ws = _workspace(tenant, db_session, email="same@example.com", name="Same Co")
    chat = _chat(db_session, ws.tenant_id)
    _say(db_session, chat, MessageRole.user, "help")

    ingest_from_operator(
        db_session,
        chat=chat,
        tenant_id=ws.tenant_id,
        text="by mail",
        actor=OperatorActor(channel=OperatorChannel.email, user_id=ws.user_id),
    )
    sent = tenant.post(
        f"/operator/chats/{chat.id}/messages", headers=ws.auth, json={"text": "by console"}
    )
    assert sent.status_code == 200, sent.text

    messages = tenant.get(f"/operator/sessions/{chat.session_id}", headers=ws.auth).json()["messages"]
    by_mail, by_console = messages[1], messages[2]
    assert {k: v for k, v in by_mail.items() if k not in ("id", "content", "created_at")} == {
        k: v for k, v in by_console.items() if k not in ("id", "content", "created_at")
    }
    assert by_mail["role"] == by_console["role"] == "operator"
    assert by_mail["author_label"] == by_console["author_label"] == "same@example.com"


def test_session_logs_carry_operator_turns(tenant: TestClient, db_session: Session) -> None:
    ws = _workspace(tenant, db_session, email="logs@example.com", name="Logs Co")
    chat = _chat(db_session, ws.tenant_id)
    _say(db_session, chat, MessageRole.user, "help")
    _say(db_session, chat, MessageRole.operator, "here", operator_user_id=ws.user_id)

    resp = tenant.get(f"/chat/logs/session/{chat.session_id}", headers=ws.auth)

    assert resp.status_code == 200, resp.text
    assert [m["role"] for m in resp.json()["messages"]] == ["user", "operator"]


# --------------------------------------------------------------------------
# The seat gate
# --------------------------------------------------------------------------


def test_a_member_without_a_seat_reads_but_cannot_act(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="noseat@example.com", name="No Seat Co", seated=False)
    chat = _chat(db_session, ws.tenant_id)
    _ticket(db_session, chat)

    assert tenant.get("/operator/inbox", headers=ws.auth).status_code == 200
    assert tenant.get("/operator/inbox/summary", headers=ws.auth).status_code == 200
    assert tenant.get(f"/operator/sessions/{chat.session_id}", headers=ws.auth).status_code == 200

    for path, payload in (
        ("take", None),
        ("messages", {"text": "hi"}),
        ("release", None),
        ("resolve", {}),
    ):
        resp = tenant.post(f"/operator/chats/{chat.id}/{path}", headers=ws.auth, json=payload)
        assert resp.status_code == 403, (path, resp.text)


def test_tenants_me_reports_the_seat(tenant: TestClient, db_session: Session) -> None:
    ws = _workspace(tenant, db_session, email="me@example.com", name="Me Co", seated=False)

    assert tenant.get("/tenants/me", headers=ws.auth).json()["has_seat"] is False
    assert tenant.put("/tenants/members/me/seat", headers=ws.auth).status_code == 200
    assert tenant.get("/tenants/me", headers=ws.auth).json()["has_seat"] is True


# --------------------------------------------------------------------------
# Resolved
# --------------------------------------------------------------------------


def test_resolve_closes_the_request_everywhere(tenant: TestClient, db_session: Session) -> None:
    ws = _workspace(tenant, db_session, email="resolve@example.com", name="Resolve Co")
    chat = _chat(db_session, ws.tenant_id)
    ticket = _ticket(db_session, chat)
    mint_reply_token(ticket, db_session)
    db_session.commit()
    assert ticket.reply_token is not None
    sent = tenant.post(f"/operator/chats/{chat.id}/messages", headers=ws.auth, json={"text": "fixed"})
    assert sent.status_code == 200, sent.text
    assert tenant.get("/operator/inbox", headers=ws.auth).json()["attention_count"] == 1

    resp = tenant.post(
        f"/operator/chats/{chat.id}/resolve", headers=ws.auth, json={"resolution_text": "refund sent"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_ticket_numbers"] == [ticket.ticket_number]
    assert body["chat"]["operator_state"] == "bot"
    assert body["chat"]["assigned_operator_id"] is None

    db_session.expire_all()
    ticket = db_session.get(EscalationTicket, ticket.id)
    assert ticket.status is EscalationStatus.resolved
    assert ticket.resolution_text == "refund sent"
    assert ticket.resolved_at is not None
    assert ticket.reply_token_revoked_at is not None
    refreshed = db_session.get(Chat, chat.id)
    assert refreshed.operator_state is OperatorState.bot
    assert refreshed.operator_released_at is not None

    queue = tenant.get("/operator/inbox", headers=ws.auth).json()
    assert queue["items"] == []
    assert queue["attention_count"] == 0
    thread = tenant.get(f"/operator/sessions/{chat.session_id}", headers=ws.auth).json()
    assert thread["handoff_state"] == "bot"
    assert thread["ticket"]["status"] == "resolved"


def test_resolve_closes_every_active_ticket_of_the_chat(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="two@example.com", name="Two Co")
    chat = _chat(db_session, ws.tenant_id)
    first = _ticket(db_session, chat, status=EscalationStatus.in_progress)
    second = _ticket(db_session, chat)
    closed = _ticket(db_session, chat, status=EscalationStatus.auto_closed)

    resp = tenant.post(f"/operator/chats/{chat.id}/resolve", headers=ws.auth, json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["resolved_ticket_numbers"] == [first.ticket_number, second.ticket_number]
    db_session.expire_all()
    assert db_session.get(EscalationTicket, closed.id).status is EscalationStatus.auto_closed


def test_resolving_a_chat_with_no_ticket_just_hands_it_back(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="none@example.com", name="None Co")
    chat = _chat(db_session, ws.tenant_id)
    assert tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth).status_code == 200

    resp = tenant.post(f"/operator/chats/{chat.id}/resolve", headers=ws.auth, json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["resolved_ticket_numbers"] == []
    assert resp.json()["chat"]["operator_state"] == "bot"

    # A second resolve changes nothing and still answers.
    again = tenant.post(f"/operator/chats/{chat.id}/resolve", headers=ws.auth, json={})
    assert again.status_code == 200, again.text
    assert again.json()["chat"]["operator_released_at"] == resp.json()["chat"]["operator_released_at"]


def test_after_resolve_the_bot_answers_the_next_turn(
    tenant: TestClient, db_session: Session
) -> None:
    """The visitor is not left talking to a silent, released operator."""
    from backend.operator.service import OperatorActor, OperatorChannel, resolve_from_operator

    ws = _workspace(tenant, db_session, email="next@example.com", name="Next Co")
    chat = _chat(db_session, ws.tenant_id)
    _ticket(db_session, chat)
    assert tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth).status_code == 200
    db_session.expire_all()
    chat = db_session.get(Chat, chat.id)

    resolve_from_operator(
        db_session,
        chat=chat,
        actor=OperatorActor(channel=OperatorChannel.console, user_id=ws.user_id),
    )

    db_session.expire_all()
    chat = db_session.get(Chat, chat.id)
    assert chat.operator_state is OperatorState.bot
    assert chat.assigned_operator_id is None
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_pre_confirm_pending is False


# --------------------------------------------------------------------------
# Tenant boundary
# --------------------------------------------------------------------------


def test_a_foreign_session_or_chat_is_unreachable(
    tenant: TestClient, db_session: Session
) -> None:
    mine = _workspace(tenant, db_session, email="mine@example.com", name="Mine Co")
    theirs = _workspace(tenant, db_session, email="theirs@example.com", name="Theirs Co")
    chat = _chat(db_session, theirs.tenant_id)
    _ticket(db_session, chat)

    assert tenant.get("/operator/inbox?scope=all", headers=mine.auth).json()["items"] == []
    assert tenant.get(f"/operator/sessions/{chat.session_id}", headers=mine.auth).status_code == 404
    for path, payload in (
        ("take", None),
        ("messages", {"text": "hi"}),
        ("release", None),
        ("resolve", {}),
    ):
        resp = tenant.post(f"/operator/chats/{chat.id}/{path}", headers=mine.auth, json=payload)
        assert resp.status_code == 404, (path, resp.text)


# --------------------------------------------------------------------------
# The notification e-mail points at the console
# --------------------------------------------------------------------------


def _notify(db: Session, tenant_id: uuid.UUID, chat: Chat) -> str:
    ticket = _ticket(db, chat, user_email="visitor@acme.io")
    workspace = db.get(Tenant, tenant_id)
    with patch("backend.escalation.service.send_email") as send_email:
        _notify_tenant_new_ticket(workspace, ticket, db)
    send_email.assert_called_once()
    return send_email.call_args.args[2]


def test_a_seated_workspace_is_offered_the_dashboard(
    tenant: TestClient, db_session: Session, monkeypatch
) -> None:
    from backend.core.config import settings

    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example/")
    ws = _workspace(tenant, db_session, email="mail@example.com", name="Mail Co")
    chat = _chat(db_session, ws.tenant_id)

    body = _notify(db_session, ws.tenant_id, chat)

    assert f"https://app.example/inbox?session={chat.session_id}" in body


def test_a_seatless_workspace_is_not_sent_to_a_read_only_console(
    tenant: TestClient, db_session: Session
) -> None:
    ws = _workspace(tenant, db_session, email="nomail@example.com", name="No Mail Co", seated=False)
    chat = _chat(db_session, ws.tenant_id)

    body = _notify(db_session, ws.tenant_id, chat)

    assert "/inbox" not in body


def test_a_colleague_token_still_gates_the_console(
    tenant: TestClient, db_session: Session
) -> None:
    """A seated colleague's token works on the write routes like the owner's."""
    ws = _workspace(tenant, db_session, email="own@example.com", name="Own Co", seated=False)
    colleague = _colleague(db_session, ws.tenant_id, email="col@own.example")
    token, _ = create_token_for_user(colleague)
    chat = _chat(db_session, ws.tenant_id)

    resp = tenant.post(
        f"/operator/chats/{chat.id}/take", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_operator_email"] == "col@own.example"
