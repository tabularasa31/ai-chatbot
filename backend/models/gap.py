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
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.gap_analyzer.enums import (
    GapClusterStatus,
    GapDismissReason,
    GapDocTopicStatus,
    GapJobKind,
    GapJobStatus,
    GapSource,
)
from backend.models.base import Base, _utcnow


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
    draft_title = Column(Text, nullable=True)
    draft_question = Column(Text, nullable=True)
    draft_markdown = Column(Text, nullable=True)
    draft_language = Column(String(8), nullable=True)
    draft_updated_at = Column(DateTime, nullable=True)
    published_faq_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_faq.id", ondelete="SET NULL", use_alter=True, name="fk_gap_clusters_published_faq_id"),
        nullable=True,
    )


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
