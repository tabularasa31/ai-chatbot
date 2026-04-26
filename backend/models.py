from __future__ import annotations

import datetime as dt
import enum
import uuid

from pgvector.sqlalchemy import Vector
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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import declarative_base, relationship

from backend.core.utils import generate_public_id
from backend.gap_analyzer.enums import (
    GapClusterStatus,
    GapDismissReason,
    GapDocTopicStatus,
    GapJobKind,
    GapJobStatus,
    GapSource,
)

Base = declarative_base()


# Map PostgreSQL-specific types to SQLite-compatible equivalents for tests.
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
    return dt.datetime.now(dt.UTC)


class DocumentType(str, enum.Enum):
    pdf = "pdf"
    markdown = "markdown"
    swagger = "swagger"
    url = "url"
    docx = "docx"
    plaintext = "plaintext"


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
    email: str | None = None
    name: str | None = None
    plan_tier: str | None = Field(
        default=None,
        description='e.g. "free" | "starter" | "growth" | "pro" | "enterprise"',
    )
    audience_tag: str | None = None
    company: str | None = None
    locale: str | None = None


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
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL", use_alter=True, name="fk_users_tenant_id"),
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
    role = Column(String(32), nullable=False, server_default="owner")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    tenant = relationship(
        "Tenant",
        back_populates="members",
        foreign_keys="User.tenant_id",
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = Column(String(255), nullable=False)
    api_key = Column(String(35), unique=True, nullable=False, index=True)
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
    settings = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    members = relationship("User", back_populates="tenant", foreign_keys="User.tenant_id")
    bots = relationship(
        "Bot",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    documents = relationship(
        "Document",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    url_sources = relationship(
        "UrlSource",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    quick_answers = relationship(
        "QuickAnswer",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    chats = relationship(
        "Chat",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    escalation_tickets = relationship(
        "EscalationTicket",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Bot(Base):
    __tablename__ = "bots"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    public_id = Column(
        String(21),
        unique=True,
        nullable=False,
        index=True,
        default=generate_public_id,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    disclosure_config = Column(JSON, nullable=True, default=None)
    agent_instructions = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    tenant = relationship("Tenant", back_populates="bots")


class Document(Base):
    __tablename__ = "documents"

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
    source_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("url_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    content_hash = Column(String(64), nullable=True, index=False)
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

    tenant = relationship("Tenant", back_populates="documents")
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
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
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

    tenant = relationship("Tenant", back_populates="url_sources")
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
    quick_answers = relationship(
        "QuickAnswer",
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


class QuickAnswer(Base):
    __tablename__ = "quick_answers"

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
    source_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("url_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key = Column(String(64), nullable=False)
    value = Column(Text, nullable=False)
    source_url = Column(Text, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    detected_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        UniqueConstraint("source_id", "key", name="uq_quick_answers_source_key"),
    )

    tenant = relationship("Tenant", back_populates="quick_answers")
    source = relationship("UrlSource", back_populates="quick_answers")


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
    # Vector column: 1536 dimensions for text-embedding-3-small.
    # Uses pgvector extension. Falls back to TEXT in SQLite (tests).
    # HNSW index (ix_embeddings_vector_hnsw) is created in migration dd643d1a544a;
    # document_id has index=True above.
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


class TenantProfile(Base):
    """Per-tenant (client) extracted knowledge profile."""

    __tablename__ = "tenant_profiles"

    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    product_name = Column(Text, nullable=True)
    escalation_language = Column(String(32), nullable=True)
    modules = Column(JSON, nullable=False, default=list)
    glossary = Column(JSON, nullable=False, default=list)
    aliases = Column(JSON, nullable=False, default=list)
    support_email = Column(Text, nullable=True)
    support_urls = Column(JSON, nullable=False, default=list)
    escalation_policy = Column(Text, nullable=True)
    extraction_status = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )  # 'pending' | 'done' | 'failed'
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    tenant = relationship("Tenant")


class TenantFaq(Base):
    """Per-tenant FAQ candidates extracted from documentation."""

    __tablename__ = "tenant_faq"

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
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    question_embedding = Column(Vector(1536), nullable=True)
    confidence = Column(Float, nullable=True)
    source = Column(Text, nullable=True)  # 'docs' | 'logs' | 'swagger'
    approved = Column(Boolean, nullable=False, default=False, server_default="false")
    # Phase 4: explainability fields (only populated for source='logs')
    cluster_size = Column(Integer, nullable=True)
    source_message_ids = Column(JSON, nullable=True)  # list of up to 10 message IDs
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    tenant = relationship("Tenant")


class LogAnalysisState(Base):
    """Per-tenant state for the chat-log analysis job (Phase 4)."""

    __tablename__ = "log_analysis_state"

    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_run_at = Column(DateTime, nullable=True)
    # Primary watermark: timestamp-based, resilient to UUID/sharded IDs
    last_run_started_at = Column(DateTime, nullable=True)
    # Auxiliary watermark: last processed message ID for dedup within batch
    last_processed_id = Column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    messages_since_last_run = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    is_running = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    last_run_status = Column(Text, nullable=True)  # 'ok' | 'failed' | 'skipped_no_data'
    last_run_faq_created = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    last_run_aliases_created = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    analysis_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    tenant = relationship("Tenant")


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
    ended_at = Column(DateTime, nullable=True)
    clarification_count = Column(Integer, nullable=False, default=0, server_default="0")
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


class GapCluster(Base):
    __tablename__ = "gap_clusters"

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
    label = Column(Text, nullable=True)
    centroid = Column(Vector(1536), nullable=True)
    question_count = Column(Integer, nullable=False, default=0, server_default="0")
    aggregate_signal_weight = Column(
        Float,
        nullable=False,
        default=0.0,
        server_default="0",
    )
    coverage_score = Column(Float, nullable=True)
    status = Column(
        Enum(GapClusterStatus, native_enum=False),
        nullable=False,
        default=GapClusterStatus.active,
        server_default=GapClusterStatus.active.value,
    )
    linked_doc_topic_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "gap_doc_topics.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_gap_clusters_linked_doc_topic_id",
        ),
        nullable=True,
    )
    language_coverage = Column(JSON, nullable=True, default=None)
    is_new = Column(Boolean, nullable=False, default=True, server_default="true")
    question_count_at_dismissal = Column(Integer, nullable=True)
    last_computed_at = Column(DateTime, nullable=True)
    last_question_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class GapDocTopic(Base):
    __tablename__ = "gap_doc_topics"

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
    topic_label = Column(Text, nullable=True)
    topic_embedding = Column(Vector(1536), nullable=True)
    coverage_score = Column(Float, nullable=True)
    status = Column(
        Enum(GapDocTopicStatus, native_enum=False),
        nullable=False,
        default=GapDocTopicStatus.active,
        server_default=GapDocTopicStatus.active.value,
    )
    example_questions = Column(ARRAY(Text), nullable=True)
    extraction_chunk_hash = Column(Text, nullable=True)
    linked_cluster_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "gap_clusters.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_gap_doc_topics_linked_cluster_id",
        ),
        nullable=True,
    )
    language = Column(String(8), nullable=True)
    is_new = Column(Boolean, nullable=False, default=True, server_default="true")
    extracted_at = Column(DateTime, nullable=True)


class GapQuestion(Base):
    __tablename__ = "gap_questions"

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
    question_text = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    cluster_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("gap_clusters.id", ondelete="SET NULL"),
        nullable=True,
    )
    gap_signal_weight = Column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    answer_confidence = Column(Float, nullable=True)
    had_fallback = Column(Boolean, nullable=False, default=False, server_default="false")
    had_escalation = Column(Boolean, nullable=False, default=False, server_default="false")
    language = Column(String(8), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class GapDismissal(Base):
    __tablename__ = "gap_dismissals"

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
    source = Column(
        Enum(GapSource, native_enum=False),
        nullable=False,
    )
    gap_id = Column(PG_UUID(as_uuid=True), nullable=False)
    topic_label = Column(Text, nullable=True)
    topic_label_embedding = Column(Vector(1536), nullable=True)
    reason = Column(
        Enum(GapDismissReason, native_enum=False),
        nullable=False,
    )
    dismissed_by = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    dismissed_at = Column(DateTime, nullable=False, default=_utcnow)


class GapQuestionMessageLink(Base):
    __tablename__ = "gap_question_message_links"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    gap_question_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("gap_questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_message_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    assistant_message_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id = Column(PG_UUID(as_uuid=True), nullable=False)
    attempt_index = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class GapAnalyzerJob(Base):
    __tablename__ = "gap_analyzer_jobs"

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
    job_kind = Column(
        Enum(GapJobKind, native_enum=False),
        nullable=False,
    )
    status = Column(
        Enum(GapJobStatus, native_enum=False),
        nullable=False,
        default=GapJobStatus.queued,
        server_default=GapJobStatus.queued.value,
    )
    trigger = Column(String(32), nullable=False, default="manual", server_default="manual")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=3, server_default="3")
    available_at = Column(DateTime, nullable=False, default=_utcnow)
    leased_at = Column(DateTime, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


Index(
    "ix_gap_clusters_tenant_status",
    GapCluster.tenant_id,
    GapCluster.status,
)
Index(
    "ix_gap_clusters_tenant_signal_weight",
    GapCluster.tenant_id,
    GapCluster.aggregate_signal_weight,
)
Index(
    "ix_gap_clusters_linked_doc_topic_id",
    GapCluster.linked_doc_topic_id,
    postgresql_where=GapCluster.linked_doc_topic_id.is_not(None),
)
Index(
    "ix_gap_doc_topics_tenant_status",
    GapDocTopic.tenant_id,
    GapDocTopic.status,
)
Index(
    "ix_gap_doc_topics_linked_cluster_id",
    GapDocTopic.linked_cluster_id,
    postgresql_where=GapDocTopic.linked_cluster_id.is_not(None),
)
Index(
    "ix_gap_doc_topics_tenant_extraction_hash",
    GapDocTopic.tenant_id,
    GapDocTopic.extraction_chunk_hash,
    postgresql_where=GapDocTopic.extraction_chunk_hash.is_not(None),
)
Index(
    "ix_gap_questions_tenant_cluster",
    GapQuestion.tenant_id,
    GapQuestion.cluster_id,
)
Index(
    "ix_gap_questions_tenant_signal_weight",
    GapQuestion.tenant_id,
    GapQuestion.gap_signal_weight,
)
Index(
    "ix_gap_dismissals_tenant_gap",
    GapDismissal.tenant_id,
    GapDismissal.source,
    GapDismissal.gap_id,
)
Index(
    "ix_gap_dismissals_dismissed_by",
    GapDismissal.dismissed_by,
)
Index(
    "ix_gap_dismissals_tenant_dismissed_at",
    GapDismissal.tenant_id,
    GapDismissal.dismissed_at.desc(),
)
Index(
    "ix_gap_question_links_gap_question",
    GapQuestionMessageLink.gap_question_id,
)
Index(
    "ix_gap_question_links_user_message",
    GapQuestionMessageLink.user_message_id,
)
Index(
    "ix_gap_question_links_assistant_message",
    GapQuestionMessageLink.assistant_message_id,
    unique=True,
)
Index(
    "ix_gap_question_links_session_id",
    GapQuestionMessageLink.session_id,
)
Index(
    "ix_gap_jobs_status_available",
    GapAnalyzerJob.status,
    GapAnalyzerJob.available_at,
)
Index(
    "ix_gap_jobs_tenant_kind_status",
    GapAnalyzerJob.tenant_id,
    GapAnalyzerJob.job_kind,
    GapAnalyzerJob.status,
)
Index(
    "ix_gap_jobs_expired_lease_in_progress",
    GapAnalyzerJob.lease_expires_at,
    postgresql_where=GapAnalyzerJob.status == GapJobStatus.in_progress.value,
)


class PiiEvent(Base):
    __tablename__ = "pii_events"

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


class ContactSession(Base):
    """Cross-session history for identified users (v2+); v1 only persists schema."""

    __tablename__ = "contact_sessions"

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
    contact_id = Column(String(255), nullable=False, index=True)
    email = Column(String(255), nullable=True)
    name = Column(String(255), nullable=True)
    plan_tier = Column(String(64), nullable=True)
    audience_tag = Column(String(128), nullable=True)
    session_started_at = Column(DateTime, nullable=False, default=_utcnow)
    session_ended_at = Column(DateTime, nullable=True)
    conversation_turns = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, default=_utcnow)


Index(
    "ix_contact_sessions_tenant_contact",
    ContactSession.tenant_id,
    ContactSession.contact_id,
)
Index(
    "uq_contact_sessions_tenant_contact_active",
    ContactSession.tenant_id,
    ContactSession.contact_id,
    unique=True,
    postgresql_where=ContactSession.session_ended_at.is_(None),
    sqlite_where=ContactSession.session_ended_at.is_(None),
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
