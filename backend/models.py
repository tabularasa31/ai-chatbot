from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import declarative_base, relationship

from backend.core.utils import generate_public_id

Base = declarative_base()


# Позволяем использовать UUID и ARRAY в SQLite (для тестов),
# мапя их на совместимые типы.
@compiles(PG_UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "CHAR(36)"


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "TEXT"


@compiles(Vector, "sqlite")
def compile_vector_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "TEXT"  # Store as text in SQLite (tests only)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class DocumentType(str, enum.Enum):
    pdf = "pdf"
    markdown = "markdown"
    swagger = "swagger"
    url = "url"


class DocumentStatus(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    embedding = "embedding"
    error = "error"


class SourceStatus(str, enum.Enum):
    queued = "queued"
    indexing = "indexing"
    ready = "ready"
    stale = "stale"
    error = "error"
    paused = "paused"


class SourceSchedule(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"
    manual = "manual"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class MessageFeedback(str, enum.Enum):
    none = "none"
    up = "up"
    down = "down"


class EscalationTrigger(str, enum.Enum):
    low_similarity = "low_similarity"
    no_documents = "no_documents"
    user_request = "user_request"
    answer_rejected = "answer_rejected"


class EscalationPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class EscalationStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"


class PiiEventDirection(str, enum.Enum):
    message_storage = "message_storage"
    escalation_ticket = "escalation_ticket"
    notification_email = "notification_email"
    original_view = "original_view"
    original_delete = "original_delete"


class EscalationPhase(str, enum.Enum):
    """OpenAI escalation UX phases (fact_json), not stored on DB."""

    handoff_email_known = "handoff_email_known"
    handoff_ask_email = "handoff_ask_email"
    email_parse_failed = "email_parse_failed"
    followup_awaiting_yes_no = "followup_awaiting_yes_no"
    chat_already_closed = "chat_already_closed"


class UserContext(BaseModel):
    """Identity fields from a signed KYC token; stored on Chat and used in the pipeline."""

    model_config = {"extra": "ignore"}

    user_id: str = Field(..., min_length=1)
    email: Optional[str] = None
    name: Optional[str] = None
    plan_tier: Optional[str] = Field(
        default=None,
        description='e.g. "free" | "starter" | "growth" | "pro" | "enterprise"',
    )
    audience_tag: Optional[str] = None
    company: Optional[str] = None
    locale: Optional[str] = None


class User(Base):
    __tablename__ = "users"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False, server_default="false")
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL", use_alter=True, name="fk_users_client_id"),
        nullable=True,
        index=True,
    )
    is_verified = Column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    verification_token = Column(String(128), nullable=True, unique=True)
    verification_expires_at = Column(DateTime, nullable=True)
    reset_password_token = Column(String(128), nullable=True, unique=True)
    reset_password_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    clients = relationship(
        "Client",
        back_populates="user",
        foreign_keys="Client.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Client(Base):
    __tablename__ = "clients"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    api_key = Column(String(32), unique=True, nullable=False, index=True)
    public_id = Column(
        String(21),
        unique=True,
        nullable=False,
        index=True,
        default=generate_public_id,
    )
    openai_api_key = Column(String(500), nullable=True, default=None)
    kyc_secret_key = Column(String(512), nullable=True)
    kyc_secret_key_previous = Column(String(512), nullable=True)
    kyc_secret_previous_expires_at = Column(DateTime, nullable=True)
    kyc_secret_key_hint = Column(String(8), nullable=True)
    disclosure_config = Column(JSON, nullable=True, default=None)
    settings = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    user = relationship("User", back_populates="clients", foreign_keys="Client.user_id")
    documents = relationship(
        "Document",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    url_sources = relationship(
        "UrlSource",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    chats = relationship(
        "Chat",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    escalation_tickets = relationship(
        "EscalationTicket",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("url_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    source_url = Column(Text, nullable=True)
    file_type = Column(
        Enum(DocumentType, native_enum=False),
        nullable=False,
    )
    parsed_text = Column(Text, nullable=True)
    status = Column(
        Enum(DocumentStatus, native_enum=False),
        nullable=False,
        default=DocumentStatus.processing,
        index=True,
    )
    health_status = Column(
        JSON,
        nullable=True,
        default=None,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    client = relationship("Client", back_populates="documents")
    source = relationship("UrlSource", back_populates="documents")
    embeddings = relationship(
        "Embedding",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UrlSource(Base):
    __tablename__ = "url_sources"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=True)
    url = Column(Text, nullable=False)
    normalized_domain = Column(String(255), nullable=False, index=True)
    status = Column(
        Enum(SourceStatus, native_enum=False),
        nullable=False,
        default=SourceStatus.queued,
        server_default=SourceStatus.queued.value,
        index=True,
    )
    crawl_schedule = Column(
        Enum(SourceSchedule, native_enum=False),
        nullable=False,
        default=SourceSchedule.weekly,
        server_default=SourceSchedule.weekly.value,
    )
    exclusion_patterns = Column(JSON, nullable=True, default=None)
    pages_found = Column(Integer, nullable=True)
    pages_indexed = Column(Integer, nullable=False, default=0, server_default="0")
    chunks_created = Column(Integer, nullable=False, default=0, server_default="0")
    tokens_used = Column(Integer, nullable=False, default=0, server_default="0")
    last_crawled_at = Column(DateTime, nullable=True)
    next_crawl_at = Column(DateTime, nullable=True)
    last_refresh_requested_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    warning_message = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    client = relationship("Client", back_populates="url_sources")
    documents = relationship(
        "Document",
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs = relationship(
        "UrlSourceRun",
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UrlSourceRun(Base):
    __tablename__ = "url_source_runs"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("url_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(32), nullable=False)
    pages_found = Column(Integer, nullable=True)
    pages_indexed = Column(Integer, nullable=False, default=0, server_default="0")
    failed_urls = Column(JSON, nullable=False, default=list)
    duration_seconds = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    finished_at = Column(DateTime, nullable=True)

    source = relationship("UrlSource", back_populates="runs")


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_text = Column(Text, nullable=False)
    # Vector column: 1536 dimensions for text-embedding-3-small
    # Uses pgvector extension. Falls back to TEXT in SQLite (tests).
    vector = Column(
        Vector(1536),
        nullable=True,
    )
    # имя атрибута не может быть `metadata` (зарезервировано в SQLAlchemy),
    # поэтому оставляем имя столбца "metadata", но меняем имя Python-атрибута
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    document = relationship("Document", back_populates="embeddings")



class Chat(Base):
    __tablename__ = "chats"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
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
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    client = relationship("Client", back_populates="chats")
    messages = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EscalationTicket(Base):
    __tablename__ = "escalation_tickets"
    __table_args__ = (
        UniqueConstraint("client_id", "ticket_number", name="uq_escalation_client_ticket_number"),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
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

    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    resolved_at = Column(DateTime, nullable=True)

    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)

    client = relationship("Client", back_populates="escalation_tickets")
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


class PiiEvent(Base):
    __tablename__ = "pii_events"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    message_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    actor_user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    direction = Column(
        Enum(PiiEventDirection, native_enum=False),
        nullable=False,
        index=True,
    )
    entity_type = Column(String(64), nullable=False)
    count = Column(Integer, nullable=False, server_default="1")
    action_path = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class UserSession(Base):
    """Cross-session history for identified users (v2+); v1 only persists schema."""

    __tablename__ = "user_sessions"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(String(255), nullable=False, index=True)
    email = Column(String(255), nullable=True)
    name = Column(String(255), nullable=True)
    plan_tier = Column(String(64), nullable=True)
    audience_tag = Column(String(128), nullable=True)
    session_started_at = Column(DateTime, nullable=False, default=_utcnow)
    session_ended_at = Column(DateTime, nullable=True)
    conversation_turns = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, default=_utcnow)


Index(
    "ix_user_sessions_client_user",
    UserSession.client_id,
    UserSession.user_id,
)


class Tester(Base):
    """Internal QA tester (plain password, MVP only)."""

    __tablename__ = "testers"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    username = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    sessions = relationship(
        "EvalSession",
        back_populates="tester",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EvalSession(Base):
    __tablename__ = "eval_sessions"

    __table_args__ = (
        Index("ix_eval_sessions_tester_started", "tester_id", "started_at"),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tester_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("testers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bot_id = Column(String(64), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=_utcnow)

    tester = relationship("Tester", back_populates="sessions")
    results = relationship(
        "EvalResult",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EvalResult(Base):
    __tablename__ = "eval_results"

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('pass', 'fail')",
            name="ck_eval_results_verdict",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN ("
            "'hallucination', 'incomplete', 'wrong_generation', "
            "'off_topic', 'no_answer', 'other')",
            name="ck_eval_results_error_category",
        ),
        CheckConstraint(
            "(verdict != 'pass' OR error_category IS NULL)",
            name="ck_eval_results_pass_no_category",
        ),
        CheckConstraint(
            "(verdict != 'fail' OR error_category IS DISTINCT FROM 'other' OR "
            "(comment IS NOT NULL AND length(trim(comment)) > 0))",
            name="ck_eval_results_other_requires_comment",
        ),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question = Column(Text, nullable=False)
    bot_answer = Column(Text, nullable=False)
    verdict = Column(String(16), nullable=False)
    error_category = Column(String(32), nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    session = relationship("EvalSession", back_populates="results")


# Note: pgvector HNSW index is created via migration, not here
# CREATE INDEX ON embeddings USING hnsw (vector vector_cosine_ops);
# document_id already has index=True on the column
