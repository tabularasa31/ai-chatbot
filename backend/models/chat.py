from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base, _utcnow
from backend.models.enums import (
    EscalationPriority,
    EscalationStatus,
    EscalationTrigger,
    MessageFeedback,
    MessageRole,
)


class MessageEmbedding(Base):
    """Embeddings for individual chat messages (Phase 4 — log analysis)."""

    __tablename__ = "message_embeddings"

    message_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
    )
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    embedding = Column(Vector(1536), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    last_used_at = Column(DateTime, nullable=False, default=_utcnow)

    tenant = relationship("Tenant")


class Chat(Base):
    __tablename__ = "chats"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bot_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("bots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_context = Column(JSON, nullable=True)
    tokens_used = Column(
        Integer,
        nullable=False,
        server_default="0",
    )
    escalation_awaiting_ticket_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("escalation_tickets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    escalation_followup_pending = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    escalation_pre_confirm_pending = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    # Stores trigger/context for deferred ticket creation after user confirms.
    # Schema: {"trigger": str, "primary_question": str,
    #          "best_similarity_score": float|null, "retrieved_chunks": list|null}
    escalation_pre_confirm_context = Column(JSON, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    # Analytics-only marker: set by the inactivity sweeper when the
    # chat_session_ended event has been emitted. Distinct from ``ended_at``
    # (which closes the conversation and routes the FSM to the closed handler)
    # so reporting a session as ended does not make the chat un-resumable.
    session_ended_event_at = Column(DateTime, nullable=True)
    clarification_count = Column(Integer, nullable=False, default=0, server_default="0")
    # True iff the immediately preceding assistant reply was the "soft rephrase"
    # prompt emitted on a zero-RAG-hits turn. Read on the next turn to decide
    # whether a second consecutive zero-hits turn should fall through to the
    # LLM relevance model (and possibly escalate) instead of repeating the
    # soft-reply. Reset to False on any non-zero-hits assistant reply.
    last_reply_was_rephrase_prompt = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    # True iff the immediately preceding assistant reply ended awaiting a user
    # answer (a clarify/slot question, e.g. "What domain would you like me to
    # check?"). Read on the next turn by SmallTalkHandler to suppress the
    # greeting fast path so a one-word reply that answers the bot's question
    # reaches RAG instead of being greeted. Set authoritatively in
    # _finalize_persisted_messages; greeting/small-talk replies always set it
    # False (a greeting phrased as a question is rhetorical, not a prompt).
    last_reply_awaited_reply = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    last_response_language = Column(String(16), nullable=True)
    # Once True, response_language is frozen at last_response_language and
    # detection is bypassed. Set by lock heuristic in backend/chat/language.py
    # (high-confidence first user turn or two consistent reliable turns).
    language_locked = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    tenant = relationship("Tenant", back_populates="chats")
    bot = relationship("Bot")
    messages = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# Partial index for the inactivity sweeper's scan (runs every few minutes).
# Only un-reported, un-closed chats are indexed, so it stays small as the bulk
# of chats acquire the marker; it covers both the filter and the ORDER BY.
Index(
    "ix_chats_sweeper_pending",
    Chat.updated_at,
    postgresql_where=Chat.session_ended_event_at.is_(None) & Chat.ended_at.is_(None),
    sqlite_where=Chat.session_ended_event_at.is_(None) & Chat.ended_at.is_(None),
)


class EscalationTicket(Base):
    __tablename__ = "escalation_tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticket_number", name="uq_escalation_tenant_ticket_number"),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticket_number = Column(String(32), nullable=False, index=True)

    primary_question = Column(Text, nullable=False)
    primary_question_original_encrypted = Column(Text, nullable=True)
    primary_question_redacted = Column(Text, nullable=True)
    conversation_summary = Column(Text, nullable=True)

    trigger = Column(
        Enum(EscalationTrigger, native_enum=False),
        nullable=False,
        index=True,
    )
    best_similarity_score = Column(Float, nullable=True)
    retrieved_chunks_preview = Column(JSON, nullable=True)

    user_id = Column(String(255), nullable=True, index=True)
    user_email = Column(String(255), nullable=True)
    user_name = Column(String(255), nullable=True)
    plan_tier = Column(String(64), nullable=True)
    user_note = Column(Text, nullable=True)

    priority = Column(
        Enum(EscalationPriority, native_enum=False),
        nullable=False,
        default=EscalationPriority.medium,
        server_default="medium",
    )
    status = Column(
        Enum(EscalationStatus, native_enum=False),
        nullable=False,
        default=EscalationStatus.open,
        server_default="open",
        index=True,
    )
    resolution_text = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    # Support-inbox notification threading (see escalation_followup_email_v1).
    # `notification_message_id` is the Message-ID of the initial notify; update
    # emails point In-Reply-To/References at it so they group as replies.
    # `last_notified_at` / `last_notified_message_id` drive synchronous debounce
    # and delta selection for follow-up update emails.
    notification_message_id = Column(String(998), nullable=True)
    last_notified_at = Column(DateTime, nullable=True)
    last_notified_message_id = Column(PG_UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)

    tenant = relationship("Tenant", back_populates="escalation_tickets")
    chat = relationship("Chat", foreign_keys=[chat_id])


class Message(Base):
    __tablename__ = "messages"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(
        Enum(MessageRole, native_enum=False),
        nullable=False,
    )
    content = Column(Text, nullable=False)
    content_original_encrypted = Column(Text, nullable=True)
    content_redacted = Column(Text, nullable=True)
    source_documents = Column(
        ARRAY(PG_UUID(as_uuid=True)),
        nullable=True,
    )
    feedback = Column(
        Enum(MessageFeedback, native_enum=False),
        nullable=False,
        default=MessageFeedback.none,
    )
    ideal_answer = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    chat = relationship("Chat", back_populates="messages")
