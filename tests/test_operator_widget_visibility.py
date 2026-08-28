"""The visitor's side of the handoff: seeing the human, and seeing them soon.

An operator's reply used to be dropped twice on its way to the widget — once
by the history query's role filter, once by the widget's own hydration — so a
human could answer a customer who never saw a word of it. These cover the
backend half: the reply survives ``/widget/history``, it arrives through the
cursor endpoint without a reload, and the byline the widget renders it under
comes back localized.

Also here: the other direction. While a chat is live the bot says nothing, so
the visitor's messages have to reach the operator by e-mail or the operator is
answering into silence.
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

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


def _conversation(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    operator_state: OperatorState = OperatorState.live,
) -> Chat:
    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        operator_state=operator_state,
        operator_joined_at=_utcnow(),
    )
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


def test_history_carries_the_operators_reply(
    tenant: TestClient, db_session: Session
) -> None:
    """Acceptance criterion 7 — the reply survives the history query."""
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="hist@example.com", name="History Co"
    )
    chat = _conversation(db_session, tenant_id)
    _say(db_session, chat, MessageRole.user, "Where is my refund?")
    _say(db_session, chat, MessageRole.operator, "I have issued it just now.")

    resp = tenant.get(
        f"/widget/history?bot_id={bot_id}&session_id={chat.session_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [m["role"] for m in body["messages"]] == ["user", "operator"]
    assert body["messages"][1]["content"] == "I have issued it just now."
    # The cursor the widget starts polling from.
    assert body["messages"][1]["id"]
    assert body["handoff_state"] == "live"
    assert body["operator_label"] == "Support"


def test_history_reports_waiting_when_a_request_is_unanswered(
    tenant: TestClient, db_session: Session
) -> None:
    """The third state is derived, never stored."""
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="waiting@example.com", name="Waiting Co"
    )
    chat = _conversation(db_session, tenant_id, operator_state=OperatorState.bot)
    _say(db_session, chat, MessageRole.user, "I need a human.")
    db_session.add(
        EscalationTicket(
            tenant_id=tenant_id,
            ticket_number="ESC-7001",
            primary_question="I need a human.",
            trigger=EscalationTrigger.user_request,
            status=EscalationStatus.open,
            chat_id=chat.id,
        )
    )
    db_session.commit()

    body = tenant.get(
        f"/widget/history?bot_id={bot_id}&session_id={chat.session_id}"
    ).json()
    assert body["handoff_state"] == "waiting"


def test_history_reports_bot_when_nobody_is_needed(
    tenant: TestClient, db_session: Session
) -> None:
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="botstate@example.com", name="Bot State Co"
    )
    chat = _conversation(db_session, tenant_id, operator_state=OperatorState.bot)
    _say(db_session, chat, MessageRole.user, "hello")

    body = tenant.get(
        f"/widget/history?bot_id={bot_id}&session_id={chat.session_id}"
    ).json()
    assert body["handoff_state"] == "bot"


def test_the_cursor_endpoint_returns_only_what_came_after(
    tenant: TestClient, db_session: Session
) -> None:
    """Acceptance criterion 8 — the reply arrives without a reload."""
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="poll@example.com", name="Poll Co"
    )
    chat = _conversation(db_session, tenant_id)
    first = _say(db_session, chat, MessageRole.user, "Where is my refund?")

    empty = tenant.get(
        f"/widget/messages?bot_id={bot_id}&session_id={chat.session_id}"
        f"&after_message_id={first.id}"
    ).json()
    assert empty["messages"] == []
    assert empty["handoff_state"] == "live"

    _say(db_session, chat, MessageRole.operator, "Issued just now.")

    tail = tenant.get(
        f"/widget/messages?bot_id={bot_id}&session_id={chat.session_id}"
        f"&after_message_id={first.id}"
    ).json()
    assert [m["role"] for m in tail["messages"]] == ["operator"]
    assert tail["messages"][0]["content"] == "Issued just now."


def test_the_cursor_endpoint_without_a_cursor_returns_the_conversation(
    tenant: TestClient, db_session: Session
) -> None:
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="nocursor@example.com", name="No Cursor Co"
    )
    chat = _conversation(db_session, tenant_id)
    _say(db_session, chat, MessageRole.user, "hi")
    _say(db_session, chat, MessageRole.operator, "hello")

    body = tenant.get(
        f"/widget/messages?bot_id={bot_id}&session_id={chat.session_id}"
    ).json()
    assert [m["role"] for m in body["messages"]] == ["user", "operator"]
    assert body["cursor_stale"] is False


def test_a_cursor_from_another_conversation_asks_for_a_rebootstrap(
    tenant: TestClient, db_session: Session
) -> None:
    """Splicing an unrelated tail on would duplicate what is on screen."""
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="stale@example.com", name="Stale Co"
    )
    chat = _conversation(db_session, tenant_id)
    _say(db_session, chat, MessageRole.user, "hi")

    body = tenant.get(
        f"/widget/messages?bot_id={bot_id}&session_id={chat.session_id}"
        f"&after_message_id={uuid.uuid4()}"
    ).json()
    assert body["cursor_stale"] is True
    assert body["messages"] == []


def test_the_byline_is_localized_into_the_conversations_language(
    tenant: TestClient, db_session: Session
) -> None:
    """Canonical English, translated at runtime — never a hardcoded table."""
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="label@example.com", name="Label Co"
    )
    chat = _conversation(db_session, tenant_id)
    chat.last_response_language = "ru"
    db_session.add(chat)
    db_session.commit()
    _say(db_session, chat, MessageRole.operator, "Готово.")

    localize = Mock()
    localize.return_value.text = "Поддержка"

    async def _fake_localize(**kwargs):
        assert kwargs["canonical_text"] == "Support"
        assert kwargs["target_language"] == "ru"
        return localize(**kwargs)

    with patch(
        "backend.widget.routes.async_localize_text_to_language_result", _fake_localize
    ):
        body = tenant.get(
            f"/widget/history?bot_id={bot_id}&session_id={chat.session_id}"
        ).json()

    assert body["operator_label"] == "Поддержка"


def test_no_localization_is_paid_for_when_no_human_has_written(
    tenant: TestClient, db_session: Session
) -> None:
    """The overwhelming majority of conversations must cost nothing here."""
    _token, bot_id, tenant_id = _bot_and_tenant(
        tenant, db_session, email="nolabel@example.com", name="No Label Co"
    )
    chat = _conversation(db_session, tenant_id, operator_state=OperatorState.bot)
    chat.last_response_language = "ru"
    db_session.add(chat)
    db_session.commit()
    _say(db_session, chat, MessageRole.user, "привет")

    localize = Mock(side_effect=AssertionError("must not localize without an operator"))
    with patch("backend.widget.routes.async_localize_text_to_language_result", localize):
        body = tenant.get(
            f"/widget/history?bot_id={bot_id}&session_id={chat.session_id}"
        ).json()

    assert body["operator_label"] == "Support"
    localize.assert_not_called()


# --------------------------------------------------------------------------
# The other direction
# --------------------------------------------------------------------------


def test_the_visitors_message_reaches_the_operator_by_email(
    mock_openai_client: Mock, tenant: TestClient, db_session: Session
) -> None:
    """Acceptance criterion 4.

    The bot is muted, so this e-mail is the operator's only sight of what the
    visitor just said. It threads under the original notification, so it lands
    in the conversation they are already reading.
    """
    token = register_and_verify_user(
        tenant, db_session, email="reach@example.com"
    )
    resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Reach Co"},
    )
    assert resp.status_code == 201, resp.text
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(resp.json()["id"])
    api_key = resp.json()["api_key"]

    chat = _conversation(db_session, tenant_id)
    db_session.add(
        EscalationTicket(
            tenant_id=tenant_id,
            ticket_number="ESC-7100",
            primary_question="Where is my refund?",
            trigger=EscalationTrigger.user_request,
            status=EscalationStatus.in_progress,
            user_email="visitor@example.com",
            chat_id=chat.id,
            notification_message_id="<notify-7100@brevo>",
            last_notified_at=_utcnow(),
        )
    )
    db_session.commit()

    with patch("backend.escalation.service.send_email", return_value="<upd@brevo>") as send:
        turn = tenant.post(
            "/chat",
            headers={"X-API-Key": api_key},
            json={
                "question": "Any news on that refund?",
                "session_id": str(chat.session_id),
            },
        )

    assert turn.status_code == 200, turn.text
    assert turn.json()["text"] == ""
    send.assert_called_once()
    assert "Any news on that refund?" in send.call_args.args[2]
    headers = send.call_args.kwargs["extra_headers"]
    assert headers["In-Reply-To"] == "<notify-7100@brevo>"


def test_a_live_chat_with_no_ticket_sends_nothing(
    mock_openai_client: Mock, tenant: TestClient, db_session: Session
) -> None:
    """No request to thread under means no e-mail, and no crash either."""
    token = register_and_verify_user(
        tenant, db_session, email="noticket@example.com"
    )
    resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Ticket Co"},
    )
    assert resp.status_code == 201, resp.text
    set_client_openai_key(tenant, token)
    chat = _conversation(db_session, uuid.UUID(resp.json()["id"]))

    with patch("backend.escalation.service.send_email") as send:
        turn = tenant.post(
            "/chat",
            headers={"X-API-Key": resp.json()["api_key"]},
            json={"question": "hello?", "session_id": str(chat.session_id)},
        )

    assert turn.status_code == 200, turn.text
    send.assert_not_called()
    db_session.expire_all()
    assert (
        db_session.query(Message).filter(Message.chat_id == chat.id).count() == 1
    )
