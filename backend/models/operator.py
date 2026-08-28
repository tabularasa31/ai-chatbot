"""Persisted record of the operator-served stretches of a conversation.

``chat_session_ended`` describes a chat from ``created_at`` to the moment it
went quiet, and it is emitted at most once. It cannot also describe the
stretch a human served: an operator reopens a chat that was already reported
as ended, so a second emission would restate the first event with the idle
wait folded in (doubling session counts, inflating average duration) — which
is why re-arming ``Chat.session_ended_event_at`` was reverted in 1bd8bd5.

Hence a row per stretch rather than a marker column pair on ``chats``. A
stretch is repeatable: an operator releases, the bot answers, another
operator takes over — two stretches in one chat, and columns on ``chats``
would silently overwrite the first. The row also gives the phase 2 console
something real to render (who held the conversation, for how long, whether
they ever replied), and turns the analytics event into a projection of stored
state rather than the only trace the stretch ever leaves.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.models.base import Base, _utcnow
from backend.models.enums import OperatorSessionEndReason


class OperatorSession(Base):
    """One stretch of a chat served by a human, open until it is closed.

    Opened when the chat goes ``live`` (an operator claims it, or simply
    starts typing) and closed by whatever hands it back — the explicit
    release, the lazy release on the visitor's next turn, or the sweeper's
    backstop for the chat nobody writes in again. At most one row per chat is
    open at a time; ``ended_at IS NULL`` is the open predicate.
    """

    __tablename__ = "operator_sessions"
    __table_args__ = (
        # Per-chat access: the open-row lookup on the ingest path (every
        # operator reply) and the full stretch history the console renders.
        Index("ix_operator_sessions_chat_joined", "chat_id", "joined_at"),
        # Two jobs in one index. As a *constraint* it makes "at most one open
        # stretch per chat" real rather than intended: opening is a
        # read-then-write, so two simultaneous ingests can both find nothing
        # open, and a silent second row would report one human-served stretch
        # as two `operator_session_ended` events — the double counting this
        # whole design exists to avoid. As an *index* its predicate matches
        # the sweeper's reconciliation scan exactly, which carries no tenant
        # or chat filter and so cannot use the composite above; it holds only
        # currently-open stretches, a handful of rows however large the table
        # grows.
        Index(
            "uq_operator_sessions_open",
            "chat_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
    )

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL for an unattributed reply: phase 1 accepts an inbound e-mail whose
    # From address matches no tenant user, because refusing it would lose a
    # real answer to a real customer. ``SET NULL`` so deleting a user never
    # deletes the record that the conversation was handled.
    operator_user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Signature kept when the account is deleted; see ``Message.operator_label``.
    # "Who handled this" is asked more often after someone leaves, not less.
    operator_label = Column(String(255), nullable=True)
    # The request being worked, resolved when the stretch opens. It is the
    # anchor for time-to-first-human-reply: that clock starts when the visitor
    # asked for a human, not when an operator picked the chat up. Measuring
    # from ``joined_at`` would measure nothing, since taking a chat and
    # answering in it are roughly the same moment. NULL when an operator
    # simply opened a conversation nobody had escalated — there was no ask, so
    # there is no response time to report.
    escalation_ticket_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("escalation_tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    joined_at = Column(DateTime, nullable=False, default=_utcnow)
    # First ``MessageRole.operator`` message of this stretch. NULL means the
    # operator took the request and never wrote a word — the shape
    # ``bounce_abandoned_claims`` exists to catch, visible here as stored
    # state instead of only as a ticket that bounced.
    first_reply_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    ended_reason = Column(
        Enum(OperatorSessionEndReason, native_enum=False, length=32),
        nullable=True,
    )
