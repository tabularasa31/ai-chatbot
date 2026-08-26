"""Live operator handoff, phase 0.

Covers the four behaviours the feature stands on — the bot going silent while
a human holds the chat, control coming back on its own when that human goes
quiet, exactly one winner for a contested conversation, and an operator reply
reopening a chat the visitor had closed — plus the sweeper interaction and
tenant isolation on every operator route.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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
    User,
)
from backend.models.base import _utcnow
from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


class _Workspace:
    """A verified user, their tenant, and an API key for the widget contour."""

    def __init__(self, token: str, tenant_id: uuid.UUID, api_key: str) -> None:
        self.token = token
        self.tenant_id = tenant_id
        self.api_key = api_key

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _make_workspace(
    client: TestClient,
    db: Session,
    *,
    email: str,
    name: str,
) -> _Workspace:
    token = register_and_verify_user(client, db, email=email)
    resp = client.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert resp.status_code in (200, 201), resp.text
    set_client_openai_key(client, token)
    body = resp.json()
    return _Workspace(token, uuid.UUID(body["id"]), body["api_key"])


def _seed_knowledge(db: Session, tenant_id: uuid.UUID) -> None:
    """One indexed chunk, so a RAG turn has something to answer from."""
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


def _make_chat(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    operator_state: OperatorState = OperatorState.bot,
    assigned_operator_id: uuid.UUID | None = None,
    operator_joined_at=None,
    ended_at=None,
) -> Chat:
    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        operator_state=operator_state,
        assigned_operator_id=assigned_operator_id,
        operator_joined_at=operator_joined_at,
        ended_at=ended_at,
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _second_user_in_tenant(db: Session, tenant_id: uuid.UUID, *, email: str) -> User:
    """A colleague on the same tenant.

    Created directly: invites arrive in phase 0.5, but the assignment race is
    a phase-0 guarantee and needs two operators to exercise.
    """
    user = User(
        email=email,
        password_hash="x",
        role="owner",
        is_verified=True,
        tenant_id=tenant_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _roles(db: Session, chat_id: uuid.UUID) -> list[MessageRole]:
    rows = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at)
        .all()
    )
    return [m.role for m in rows]


# --------------------------------------------------------------------------
# The bot goes silent
# --------------------------------------------------------------------------


def test_bot_produces_no_reply_while_operator_is_live(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    ws = _make_workspace(tenant, db_session, email="mute@example.com", name="Mute Co")
    _seed_knowledge(db_session, ws.tenant_id)
    _arm_openai(mock_openai_client)
    chat = _make_chat(
        db_session,
        ws.tenant_id,
        operator_state=OperatorState.live,
        operator_joined_at=_utcnow(),
    )

    resp = tenant.post(
        "/chat",
        headers={"X-API-Key": ws.api_key},
        json={"question": "When do I get my refund?", "session_id": str(chat.session_id)},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == ""
    db_session.expire_all()
    # The visitor's message is on the record; nothing was generated for it.
    assert _roles(db_session, chat.id) == [MessageRole.user]
    assert db_session.get(Chat, chat.id).operator_state is OperatorState.live


def test_visitor_message_is_persisted_verbatim_while_live(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    ws = _make_workspace(tenant, db_session, email="keep@example.com", name="Keep Co")
    _seed_knowledge(db_session, ws.tenant_id)
    _arm_openai(mock_openai_client)
    chat = _make_chat(
        db_session,
        ws.tenant_id,
        operator_state=OperatorState.live,
        operator_joined_at=_utcnow(),
    )

    resp = tenant.post(
        "/chat",
        headers={"X-API-Key": ws.api_key},
        json={"question": "my order is 12345", "session_id": str(chat.session_id)},
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    stored = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(stored) == 1
    assert stored[0].role is MessageRole.user
    # Storage keeps the original wording; redaction is an egress concern.
    assert stored[0].content == "my order is 12345"
    assert stored[0].operator_user_id is None


def test_live_chat_outranks_a_closed_chat_in_the_router() -> None:
    """OperatorHandler must sit ahead of EscalationStateMachine.

    Otherwise a chat that is both closed and live routes into the
    "chat already closed" path and the visitor is told the conversation is
    over while a human is answering them.
    """
    from backend.chat.handlers.escalation import EscalationStateMachine
    from backend.chat.handlers.operator import OperatorHandler
    from backend.chat.handlers.router import default_router

    handlers = default_router().handlers
    assert isinstance(handlers[0], OperatorHandler)
    positions = {type(h): i for i, h in enumerate(handlers)}
    assert positions[OperatorHandler] < positions[EscalationStateMachine]


# --------------------------------------------------------------------------
# Lazy release
# --------------------------------------------------------------------------


def test_lazy_release_hands_control_back_and_the_bot_answers(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """An operator who went quiet loses the chat on the visitor's next message.

    The release must not cost the visitor a turn: the same message that
    triggers it is answered by the bot.
    """
    ws = _make_workspace(tenant, db_session, email="lazy@example.com", name="Lazy Co")
    _seed_knowledge(db_session, ws.tenant_id)
    _arm_openai(mock_openai_client, answer="Refunds take 14 days.")
    operator = _second_user_in_tenant(db_session, ws.tenant_id, email="op@lazy.example")
    chat = _make_chat(
        db_session,
        ws.tenant_id,
        operator_state=OperatorState.live,
        assigned_operator_id=operator.id,
        # Well past the 15-minute default release window.
        operator_joined_at=_utcnow() - timedelta(hours=2),
    )

    resp = tenant.post(
        "/chat",
        headers={"X-API-Key": ws.api_key},
        json={"question": "When do I get my refund?", "session_id": str(chat.session_id)},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] != ""

    db_session.expire_all()
    refreshed = db_session.get(Chat, chat.id)
    assert refreshed.operator_state is OperatorState.bot
    assert refreshed.operator_released_at is not None
    # Cleared, so the next /take is not permanently blocked.
    assert refreshed.assigned_operator_id is None
    assert MessageRole.assistant in _roles(db_session, chat.id)


def test_recent_operator_activity_keeps_the_bot_muted(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The release window is measured from the last operator *message* too.

    A chat taken hours ago but answered a minute ago is actively worked, and
    releasing it would put the bot on top of a live human conversation.
    """
    ws = _make_workspace(tenant, db_session, email="recent@example.com", name="Recent Co")
    _seed_knowledge(db_session, ws.tenant_id)
    _arm_openai(mock_openai_client)
    operator = _second_user_in_tenant(db_session, ws.tenant_id, email="op@recent.example")
    chat = _make_chat(
        db_session,
        ws.tenant_id,
        operator_state=OperatorState.live,
        assigned_operator_id=operator.id,
        operator_joined_at=_utcnow() - timedelta(hours=2),
    )
    db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.operator,
            content="Looking into it now.",
            operator_user_id=operator.id,
            created_at=_utcnow() - timedelta(minutes=1),
        )
    )
    db_session.commit()

    resp = tenant.post(
        "/chat",
        headers={"X-API-Key": ws.api_key},
        json={"question": "any update?", "session_id": str(chat.session_id)},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == ""
    db_session.expire_all()
    assert db_session.get(Chat, chat.id).operator_state is OperatorState.live
    assert MessageRole.assistant not in _roles(db_session, chat.id)


# --------------------------------------------------------------------------
# Taking a conversation
# --------------------------------------------------------------------------


def test_take_claims_the_chat_and_mutes_the_bot(
    tenant: TestClient,
    db_session: Session,
) -> None:
    ws = _make_workspace(tenant, db_session, email="take@example.com", name="Take Co")
    chat = _make_chat(db_session, ws.tenant_id)

    resp = tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operator_state"] == "live"
    assert body["assigned_operator_id"] is not None
    assert body["operator_joined_at"] is not None


def test_two_takes_leave_exactly_one_winner(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The claim is a single conditional UPDATE, so the loser gets a clean 409."""
    from backend.auth.service import create_token_for_user

    ws = _make_workspace(tenant, db_session, email="race@example.com", name="Race Co")
    colleague = _second_user_in_tenant(
        db_session, ws.tenant_id, email="colleague@race.example"
    )
    colleague_token, _ = create_token_for_user(colleague)
    chat = _make_chat(db_session, ws.tenant_id)

    first = tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth)
    second = tenant.post(
        f"/operator/chats/{chat.id}/take",
        headers={"Authorization": f"Bearer {colleague_token}"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    db_session.expire_all()
    refreshed = db_session.get(Chat, chat.id)
    assert refreshed.assigned_operator_id == uuid.UUID(
        first.json()["assigned_operator_id"]
    )
    assert refreshed.assigned_operator_id != colleague.id


def test_release_returns_the_chat_to_the_bot(
    tenant: TestClient,
    db_session: Session,
) -> None:
    ws = _make_workspace(tenant, db_session, email="rel@example.com", name="Rel Co")
    chat = _make_chat(db_session, ws.tenant_id)
    assert tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth).status_code == 200

    resp = tenant.post(f"/operator/chats/{chat.id}/release", headers=ws.auth)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operator_state"] == "bot"
    assert body["assigned_operator_id"] is None
    assert body["operator_released_at"] is not None

    # Releasing again is a no-op: a retry must not overwrite the timestamp of
    # the release that actually happened.
    again = tenant.post(f"/operator/chats/{chat.id}/release", headers=ws.auth)
    assert again.status_code == 200, again.text
    assert again.json()["operator_released_at"] == body["operator_released_at"]

    # Released is takeable again — the claim predicate must not stay falsified.
    assert tenant.post(f"/operator/chats/{chat.id}/take", headers=ws.auth).status_code == 200


# --------------------------------------------------------------------------
# Operator messages
# --------------------------------------------------------------------------


def test_operator_message_is_stored_with_its_author(
    tenant: TestClient,
    db_session: Session,
) -> None:
    ws = _make_workspace(tenant, db_session, email="msg@example.com", name="Msg Co")
    chat = _make_chat(db_session, ws.tenant_id)

    resp = tenant.post(
        f"/operator/chats/{chat.id}/messages",
        headers=ws.auth,
        json={"text": "Hi, this is Support — refunds land in 14 days."},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chat"]["operator_state"] == "live"
    assert body["chat_reopened"] is False

    db_session.expire_all()
    stored = db_session.get(Message, uuid.UUID(body["message_id"]))
    assert stored.role is MessageRole.operator
    assert stored.operator_user_id is not None
    assert stored.content == "Hi, this is Support — refunds land in 14 days."
    # Answering claims an unclaimed chat: no separate "take" required.
    assert db_session.get(Chat, chat.id).assigned_operator_id == stored.operator_user_id


def test_operator_message_reopens_a_chat_the_visitor_closed(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The visitor said "no, that's all" before the operator got there.

    A person has now answered, so the conversation is evidently not over — and
    the visitor must be able to reply, which requires ``ended_at`` cleared.
    """
    ws = _make_workspace(tenant, db_session, email="reopen@example.com", name="Reopen Co")
    chat = _make_chat(
        db_session,
        ws.tenant_id,
        ended_at=_utcnow() - timedelta(minutes=20),
    )

    resp = tenant.post(
        f"/operator/chats/{chat.id}/messages",
        headers=ws.auth,
        json={"text": "Sorry for the delay — here is the answer."},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["chat_reopened"] is True
    db_session.expire_all()
    refreshed = db_session.get(Chat, chat.id)
    assert refreshed.ended_at is None
    assert refreshed.operator_state is OperatorState.live


def test_operator_message_does_not_reassign_a_colleagues_chat(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Assignment is advisory: a shared inbox has no single claimant."""
    ws = _make_workspace(tenant, db_session, email="adv@example.com", name="Adv Co")
    colleague = _second_user_in_tenant(
        db_session, ws.tenant_id, email="colleague@adv.example"
    )
    chat = _make_chat(
        db_session,
        ws.tenant_id,
        operator_state=OperatorState.live,
        assigned_operator_id=colleague.id,
        operator_joined_at=_utcnow(),
    )

    resp = tenant.post(
        f"/operator/chats/{chat.id}/messages",
        headers=ws.auth,
        json={"text": "Jumping in to help."},
    )

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(Chat, chat.id).assigned_operator_id == colleague.id
    # The message is still attributed to whoever actually wrote it.
    stored = db_session.get(Message, uuid.UUID(resp.json()["message_id"]))
    assert stored.operator_user_id != colleague.id


# --------------------------------------------------------------------------
# Sweeper
# --------------------------------------------------------------------------


def test_sweeper_leaves_a_live_chat_alone(db_session: Session) -> None:
    """Idleness is measured on visitor activity, which a working operator
    does not refresh — so a live handoff can look stale while it is being
    answered. Closing its ticket underneath the operator is exactly wrong.
    """
    from backend.core.config import settings
    from backend.jobs.chat_session_sweeper import auto_close_stale_tickets
    from backend.models import Tenant

    tenant_row = Tenant(name="Sweeper Live")
    db_session.add(tenant_row)
    db_session.commit()
    db_session.refresh(tenant_row)

    stale_at = _utcnow() - timedelta(
        seconds=settings.conversation_idle_timeout_seconds + 3600
    )
    live_chat = Chat(
        tenant_id=tenant_row.id,
        session_id=uuid.uuid4(),
        operator_state=OperatorState.live,
        created_at=stale_at,
        updated_at=stale_at,
    )
    bot_chat = Chat(
        tenant_id=tenant_row.id,
        session_id=uuid.uuid4(),
        created_at=stale_at,
        updated_at=stale_at,
    )
    db_session.add_all([live_chat, bot_chat])
    db_session.commit()

    tickets = []
    for index, chat in enumerate((live_chat, bot_chat)):
        ticket = EscalationTicket(
            tenant_id=tenant_row.id,
            ticket_number=f"ESC-{index}",
            primary_question="help",
            trigger=EscalationTrigger.low_similarity,
            status=EscalationStatus.open,
            chat_id=chat.id,
        )
        db_session.add(ticket)
        tickets.append(ticket)
    db_session.commit()

    closed = auto_close_stale_tickets(db_session)

    assert closed == 1
    db_session.expire_all()
    assert db_session.get(EscalationTicket, tickets[0].id).status is EscalationStatus.open
    assert (
        db_session.get(EscalationTicket, tickets[1].id).status
        is EscalationStatus.auto_closed
    )


# --------------------------------------------------------------------------
# Tenant isolation
# --------------------------------------------------------------------------


def test_operator_routes_are_unreachable_across_tenants(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Another tenant's chat is 404 — unreachable, not merely forbidden."""
    owner = _make_workspace(tenant, db_session, email="owner@example.com", name="Owner Co")
    outsider = _make_workspace(
        tenant, db_session, email="outsider@example.com", name="Outsider Co"
    )
    chat = _make_chat(db_session, owner.tenant_id)

    take = tenant.post(f"/operator/chats/{chat.id}/take", headers=outsider.auth)
    message = tenant.post(
        f"/operator/chats/{chat.id}/messages",
        headers=outsider.auth,
        json={"text": "let me in"},
    )
    release = tenant.post(f"/operator/chats/{chat.id}/release", headers=outsider.auth)

    assert take.status_code == 404, take.text
    assert message.status_code == 404, message.text
    assert release.status_code == 404, release.text

    db_session.expire_all()
    untouched = db_session.get(Chat, chat.id)
    assert untouched.operator_state is OperatorState.bot
    assert untouched.assigned_operator_id is None
    assert db_session.query(Message).filter(Message.chat_id == chat.id).count() == 0


def test_operator_routes_require_authentication(
    tenant: TestClient,
    db_session: Session,
) -> None:
    ws = _make_workspace(tenant, db_session, email="anon@example.com", name="Anon Co")
    chat = _make_chat(db_session, ws.tenant_id)

    assert tenant.post(f"/operator/chats/{chat.id}/take").status_code in (401, 403)
    assert (
        tenant.post(
            f"/operator/chats/{chat.id}/messages", json={"text": "hi"}
        ).status_code
        in (401, 403)
    )
    assert tenant.post(f"/operator/chats/{chat.id}/release").status_code in (401, 403)
