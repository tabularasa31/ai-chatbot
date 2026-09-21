"""An operator's reply the visitor never saw reaches them by e-mail.

The widget reports what the visitor has actually had on screen; five minutes
after an operator replies, a deferred job mails whatever is still unread to
the address the conversation knows. These cover the read receipt, the mail
decision in every branch, and the hand-off from an operator reply to the
queue.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from arq import Retry
from sqlalchemy.exc import OperationalError

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import (
    Chat,
    EscalationStatus,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    OperatorState,
)
from backend.models.base import _utcnow
from backend.operator.unread_reply import (
    UNREAD_REPLY_GRACE_SECONDS,
    mail_unread_operator_reply,
    mail_unread_operator_replies,
    mark_visitor_read,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


def _bot_and_tenant(
    client: TestClient, db: Session, *, email: str, name: str
) -> tuple[str, str, uuid.UUID]:
    token = register_and_verify_user(client, db, email=email)
    resp = client.post(
        "/tenants", headers={"Authorization": f"Bearer {token}"}, json={"name": name}
    )
    assert resp.status_code == 201, resp.text
    set_client_openai_key(client, token)
    bot = client.post(
        "/bots", headers={"Authorization": f"Bearer {token}"}, json={"name": "Test Bot"}
    )
    assert bot.status_code == 201, bot.text
    return token, bot.json()["public_id"], uuid.UUID(resp.json()["id"])


def _conversation(db: Session, tenant_id: uuid.UUID, **extra) -> Chat:
    fields = {
        "operator_state": OperatorState.live,
        "operator_joined_at": _utcnow(),
        **extra,
    }
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), **fields)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _say(db: Session, chat: Chat, role: MessageRole, content: str) -> Message:
    message = Message(chat_id=chat.id, role=role, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _ticket(db: Session, chat: Chat, *, email: str | None) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=chat.tenant_id,
        chat_id=chat.id,
        session_id=chat.session_id,
        ticket_number=f"ESC-{uuid.uuid4().hex[:4]}",
        primary_question="Where is my refund?",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.in_progress,
        user_email=email,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def _read_url(bot_id: str, chat: Chat) -> str:
    return f"/widget/messages/read?bot_id={bot_id}&session_id={chat.session_id}"


# --------------------------------------------------------------------------
# The read receipt
# --------------------------------------------------------------------------


def test_the_receipt_moves_the_cursor_forward_only(
    tenant: TestClient, db_session: Session
) -> None:
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="receipt@example.com", name="Receipt Co"
    )
    chat = _conversation(db_session, tenant_id)
    first = _say(db_session, chat, MessageRole.operator, "One")
    second = _say(db_session, chat, MessageRole.operator, "Two")

    resp = tenant.post(_read_url(bot_id, chat), json={"message_id": str(second.id)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["read_message_id"] == str(second.id)

    stale = tenant.post(_read_url(bot_id, chat), json={"message_id": str(first.id)})
    assert stale.status_code == 200
    assert stale.json()["read_message_id"] == str(second.id)

    db_session.refresh(chat)
    assert chat.visitor_read_message_id == second.id


def test_a_message_from_another_conversation_is_refused(
    tenant: TestClient, db_session: Session
) -> None:
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="foreign@example.com", name="Foreign Co"
    )
    chat = _conversation(db_session, tenant_id)
    other = _conversation(db_session, tenant_id)
    elsewhere = _say(db_session, other, MessageRole.operator, "Not yours")

    resp = tenant.post(_read_url(bot_id, chat), json={"message_id": str(elsewhere.id)})
    assert resp.status_code == 404
    db_session.refresh(chat)
    assert chat.visitor_read_message_id is None


# --------------------------------------------------------------------------
# The mail decision
# --------------------------------------------------------------------------


def _send(db: Session, chat: Chat, message: Message, *, result: str | None = "<id>"):
    with patch("backend.operator.unread_reply.send_email", return_value=result) as send:
        outcome = mail_unread_operator_replies(db, chat_id=chat.id, message_id=message.id)
    return outcome, send


def test_an_unread_reply_is_mailed_to_the_ticket_address(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="owner@example.com", name="Mail Co"
    )
    chat = _conversation(db_session, tenant_id)
    ticket = _ticket(db_session, chat, email="visitor@example.com")
    _say(db_session, chat, MessageRole.user, "Where is my refund?")
    reply = _say(db_session, chat, MessageRole.operator, "Issued just now.")

    outcome, send = _send(db_session, chat, reply)

    assert outcome == "sent"
    send.assert_called_once()
    to, subject, body = send.call_args.args
    assert to == "visitor@example.com"
    assert ticket.ticket_number in subject
    assert body == "Issued just now."
    assert send.call_args.kwargs["reply_to"] == "owner@example.com"
    db_session.refresh(chat)
    assert chat.unread_reply_mailed_message_id == reply.id


def test_a_reply_the_visitor_has_seen_is_not_mailed(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="seen@example.com", name="Seen Co"
    )
    chat = _conversation(db_session, tenant_id)
    _ticket(db_session, chat, email="visitor@example.com")
    reply = _say(db_session, chat, MessageRole.operator, "Issued just now.")
    assert mark_visitor_read(db_session, chat=chat, message_id=reply.id)

    outcome, send = _send(db_session, chat, reply)

    assert outcome == "nothing_unread"
    send.assert_not_called()


def test_the_visitors_own_reply_counts_as_having_read(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="typed@example.com", name="Typed Co"
    )
    chat = _conversation(db_session, tenant_id)
    _ticket(db_session, chat, email="visitor@example.com")
    reply = _say(db_session, chat, MessageRole.operator, "Issued just now.")
    _say(db_session, chat, MessageRole.user, "Great, thanks!")

    outcome, send = _send(db_session, chat, reply)

    assert outcome == "nothing_unread"
    send.assert_not_called()


def test_several_replies_go_out_as_one_mail(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="burst@example.com", name="Burst Co"
    )
    chat = _conversation(db_session, tenant_id)
    _ticket(db_session, chat, email="visitor@example.com")
    first = _say(db_session, chat, MessageRole.operator, "Checked your order.")
    second = _say(db_session, chat, MessageRole.operator, "Refund is on its way.")
    third = _say(db_session, chat, MessageRole.operator, "Allow 3 days.")

    outcome, send = _send(db_session, chat, first)
    assert outcome == "sent"
    body = send.call_args.args[2]
    assert body == "Checked your order.\n\nRefund is on its way.\n\nAllow 3 days."
    db_session.refresh(chat)
    assert chat.unread_reply_mailed_message_id == third.id

    for later in (second, third):
        outcome, send = _send(db_session, chat, later)
        assert outcome == "nothing_unread"
        send.assert_not_called()


def test_a_reply_after_the_mail_is_mailed_on_its_own(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="again@example.com", name="Again Co"
    )
    chat = _conversation(db_session, tenant_id)
    _ticket(db_session, chat, email="visitor@example.com")
    first = _say(db_session, chat, MessageRole.operator, "Checked your order.")
    _send(db_session, chat, first)
    db_session.refresh(chat)
    later = _say(db_session, chat, MessageRole.operator, "One more thing.")

    outcome, send = _send(db_session, chat, later)

    assert outcome == "sent"
    assert send.call_args.args[2] == "One more thing."


def test_without_an_address_nothing_is_sent(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="noaddr@example.com", name="NoAddr Co"
    )
    chat = _conversation(db_session, tenant_id)
    _ticket(db_session, chat, email="not-an-address")
    reply = _say(db_session, chat, MessageRole.operator, "Issued just now.")

    outcome, send = _send(db_session, chat, reply)

    assert outcome == "no_recipient"
    send.assert_not_called()
    db_session.refresh(chat)
    assert chat.unread_reply_mailed_message_id is None


def test_the_site_hint_address_is_used_when_there_is_no_ticket(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="hint@example.com", name="Hint Co"
    )
    chat = _conversation(
        db_session, tenant_id, user_context={"email": "known@example.com"}
    )
    reply = _say(db_session, chat, MessageRole.operator, "Issued just now.")

    outcome, send = _send(db_session, chat, reply)

    assert outcome == "sent"
    assert send.call_args.args[0] == "known@example.com"
    assert send.call_args.args[1] == "Hint Co"


def test_a_failed_send_leaves_the_marker_for_a_retry(
    tenant: TestClient, db_session: Session
) -> None:
    _token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="fail@example.com", name="Fail Co"
    )
    chat = _conversation(db_session, tenant_id)
    _ticket(db_session, chat, email="visitor@example.com")
    reply = _say(db_session, chat, MessageRole.operator, "Issued just now.")

    outcome, _send_mock = _send(db_session, chat, reply, result=None)

    assert outcome == "send_failed"
    db_session.refresh(chat)
    assert chat.unread_reply_mailed_message_id is None


@pytest.mark.asyncio
async def test_a_transient_database_error_asks_the_queue_to_retry() -> None:
    boom = OperationalError("SELECT 1", {}, Exception("connection reset"))
    with patch("backend.operator.unread_reply._mail_in_thread", side_effect=boom):
        with pytest.raises(Retry):
            await mail_unread_operator_reply(
                {"job_id": "j", "job_try": 1}, str(uuid.uuid4()), str(uuid.uuid4())
            )


@pytest.mark.asyncio
async def test_a_failed_send_asks_the_queue_to_retry() -> None:
    with patch("backend.operator.unread_reply._mail_in_thread", return_value="send_failed"):
        with pytest.raises(Retry):
            await mail_unread_operator_reply(
                {"job_id": "j", "job_try": 1}, str(uuid.uuid4()), str(uuid.uuid4())
            )


# --------------------------------------------------------------------------
# From an operator reply to the queue
# --------------------------------------------------------------------------


def test_an_operator_reply_schedules_the_five_minute_check(
    tenant: TestClient, db_session: Session
) -> None:
    token, _bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="queue@example.com", name="Queue Co"
    )
    seat = tenant.put(
        "/tenants/members/me/seat", headers={"Authorization": f"Bearer {token}"}
    )
    assert seat.status_code == 200, seat.text
    chat = _conversation(db_session, tenant_id, operator_state=OperatorState.bot)
    _say(db_session, chat, MessageRole.user, "Where is my refund?")

    enqueue = AsyncMock(return_value="job-1")
    with patch("backend.operator.unread_reply.enqueue", enqueue):
        resp = tenant.post(
            f"/operator/chats/{chat.id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "Issued just now."},
        )
    assert resp.status_code == 200, resp.text

    enqueue.assert_awaited_once()
    args, kwargs = enqueue.call_args
    assert args[0] == "mail_unread_operator_reply"
    assert args[1] == str(chat.id)
    assert args[2] == resp.json()["message_id"]
    assert kwargs["_defer_by"] == UNREAD_REPLY_GRACE_SECONDS
    assert kwargs["tenant_id"] == tenant_id
