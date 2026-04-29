from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base, _utcnow
from backend.models.enums import DocumentStatus, DocumentType, SourceSchedule, SourceStatus


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
    language = Column(String(8), nullable=True)
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
    status = Column(String(32), nullable=False, index=True)
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
    key = Column(String(64), nullable=False, index=True)
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
    # Named entities extracted from chunk_text by backend.knowledge.entity_extractor
    # (HippoRAG-style NER). Step 5 of the entity-aware retrieval epic uses this
    # list as the third RRF channel: "give me chunks whose entities overlap with
    # the entities of the user's query." JSONB on PostgreSQL with a GIN index
    # (see migration embeddings_entities_v1) so the ``?|`` lookup is O(log N).
    # NOT NULL with empty-list default so legacy rows + NER-skip cases (empty
    # query, missing key, NER error) write a deterministic empty list instead
    # of NULL, keeping the retriever's "any-of-array" predicate trivially safe.
    entities = Column(JSON, nullable=False, default=list, server_default="[]")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    document = relationship("Document", back_populates="embeddings")
