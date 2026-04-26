from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base, _utcnow


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
