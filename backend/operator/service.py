"""Operator handoff business logic.

All DB work is sync and bridged from the async routes via ``run_sync``,
matching the rest of the chat domain.

The public seam is :func:`ingest_from_operator`: one entry point for a human
reply regardless of the channel it arrived on. The dashboard route below is
its first caller; phase 1's inbound e-mail webhook is the second, and the
Telegram / Slack bridges after that. Everything channel-specific (who the
sender is, how they were authenticated) is resolved by the caller and handed
in as an :class:`OperatorActor`, so this function never grows a branch per
channel.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.models import Chat, Message, OperatorState
from backend.models.base import _utcnow


class OperatorChannel(str, enum.Enum):
    """How an operator's reply reached us.

    Recorded for observability and for the console, which must render an
    e-mail reply as a first-class answer rather than a degraded one.
    """

    console = "console"


@dataclass(frozen=True)
class OperatorActor:
    """Who is answering, independent of how the message arrived.

    ``user_id`` is ``None`` for an unattributed reply — phase 1 accepts an
    inbound e-mail whose From address matches no tenant user, because refusing
    it would lose a real answer to a real customer for the sake of a tidy
    model.
    """

    channel: OperatorChannel
    user_id: uuid.UUID | None = None


@dataclass(frozen=True)
class OperatorIngestResult:
    message: Message
    chat_reopened: bool
    claimed: bool


def taken_over_values() -> dict[str, object]:
    """The column values every "an operator has taken this chat" write shares.

    Both entry points — ``/take`` (a conditional bulk UPDATE) and
    ``/messages`` (ORM writes in :func:`ingest_from_operator`) — apply this,
    so an operator who clicks *take* and one who just starts typing leave the
    row in the same shape. They used to disagree about ``ended_at``, which
    left a claimed-but-closed chat rendering ``chat_ended: true`` in
    ``/widget/history`` with the widget input locked, so the visitor could not
    reply to the human who had just claimed them.

    **The escalation FSM flags are cleared.** A human has taken the request,
    so the bot's escalation automaton — ask for an e-mail, pre-confirm,
    "anything else?" — is obsolete. Left set, they outlive the handoff:
    ``EscalationStateMachine.can_handle`` still keys off them, so after the
    operator resolves the issue and leaves, the visitor's "great, thanks Ann!"
    gets "A support ticket was created for you." Worse while the handoff is
    live, every visitor turn is swallowed by ``OperatorHandler`` — including
    the e-mail address the bot had asked for — so the automaton waits forever
    for contact details the visitor already typed.

    ``EscalationTicket`` itself is deliberately untouched: the ticket is the
    unit of work and the operator is working it. Only the automaton state
    goes. ``session_ended_event_at`` is likewise left alone — see
    :func:`ingest_from_operator`.
    """
    return {
        "ended_at": None,
        "escalation_awaiting_ticket_id": None,
        "escalation_pre_confirm_pending": False,
        "escalation_pre_confirm_context": None,
        "escalation_awaiting_request": False,
        "escalation_followup_pending": False,
    }


def get_tenant_chat(db: Session, *, chat_id: uuid.UUID, tenant_id: uuid.UUID) -> Chat | None:
    """Fetch a chat inside the caller's tenant, or ``None``.

    The tenant filter is part of the lookup rather than a check afterwards, so
    a chat belonging to another tenant is *unreachable* — indistinguishable
    from one that does not exist — instead of merely unauthorised. Callers turn
    ``None`` into a 404.
    """
    return (
        db.query(Chat)
        .filter(Chat.id == chat_id, Chat.tenant_id == tenant_id)
        .first()
    )


def claim_chat(db: Session, *, chat_id: uuid.UUID, tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Atomically take an unclaimed chat. Returns False when someone else won.

    A single conditional UPDATE, not read-then-write: two operators clicking
    "take" on the same conversation serialize on the row, and the loser's
    ``assigned_operator_id IS NULL`` predicate is re-evaluated after the
    winner commits, so it matches zero rows. The tenant filter is repeated
    here — the predicate is the authorization boundary, and a claim must never
    be able to reach across tenants even if a caller skipped the lookup.

    Applies :func:`taken_over_values` so this and ``/messages`` agree on what
    "an operator has this chat" means — reopening a closed chat included.
    """
    updated = (
        db.query(Chat)
        .filter(
            Chat.id == chat_id,
            Chat.tenant_id == tenant_id,
            Chat.assigned_operator_id.is_(None),
        )
        .update(
            {
                **taken_over_values(),
                "assigned_operator_id": user_id,
                "operator_state": OperatorState.live,
                # Naive UTC — the column is ``DateTime`` with no timezone and
                # asyncpg refuses aware values. See ``models/base._utcnow``.
                "operator_joined_at": _utcnow(),
                "operator_released_at": None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(updated)


def release_chat(db: Session, chat: Chat) -> Chat:
    """Hand the conversation back to the bot.

    Shares :func:`backend.chat.handlers.operator.release_to_bot` with the lazy
    release on the chat path, so an explicit "return to bot" and an idle
    timeout leave the row in exactly the same shape.

    A chat already in ``bot`` is left untouched rather than re-stamped: a
    double click or a retried request must not overwrite the timestamp of the
    release that actually happened.
    """
    from backend.chat.handlers.operator import release_to_bot

    if chat.operator_state is OperatorState.bot:
        return chat

    release_to_bot(db, chat)
    db.commit()
    db.refresh(chat)
    return chat


def ingest_from_operator(
    db: Session,
    *,
    chat: Chat,
    tenant_id: uuid.UUID,
    text: str,
    actor: OperatorActor,
    optional_entity_types: set[str] | None = None,
) -> OperatorIngestResult:
    """Record a human reply in the chat thread and put the chat in ``live``.

    Three side effects beyond persisting the message, all of them consequences
    of "a person has just answered this visitor":

    * The chat goes ``live``, muting the bot for subsequent visitor turns.
    * An unclaimed chat is claimed by the actor. A chat already claimed by
      someone else is *not* reassigned — assignment is advisory, and a shared
      support inbox means two people can legitimately answer the same thread.
    * A chat the visitor had closed is reopened. Otherwise the answer would
      land in a transcript the visitor can read but cannot reply to, and
      session resume would skip the chat entirely (the widget only reattaches
      to chats with ``ended_at IS NULL``).
    * The escalation FSM flags are cleared — see :func:`taken_over_values`
      for why, and for why the ticket row itself is not touched.
    """
    from backend.chat.service import _persist_operator_message

    chat_reopened = chat.ended_at is not None
    # ``taken_over_values`` clears ``ended_at`` (reopening the chat) along with
    # the escalation FSM flags. ``session_ended_event_at`` is deliberately not
    # in it: re-arming that marker would make the sweeper emit a second
    # ``chat_session_ended`` for this chat, and that event measures
    # ``duration_ms`` from ``chat.created_at`` — so the repeat would not
    # describe the operator-served stretch, it would restate the first event
    # with the idle wait folded in. Session counts would double and average
    # duration would inflate. The operator-served stretch needs its own event,
    # measured from ``operator_joined_at``, rather than a second helping of
    # this one.
    for column, value in taken_over_values().items():
        setattr(chat, column, value)

    claimed = actor.user_id is not None and chat.assigned_operator_id is None
    if claimed:
        chat.assigned_operator_id = actor.user_id

    if chat.operator_state is not OperatorState.live:
        chat.operator_state = OperatorState.live
        chat.operator_joined_at = _utcnow()
        chat.operator_released_at = None
    db.add(chat)

    message = _persist_operator_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        content=text,
        operator_user_id=actor.user_id,
        optional_entity_types=optional_entity_types,
    )
    return OperatorIngestResult(
        message=message, chat_reopened=chat_reopened, claimed=claimed
    )
