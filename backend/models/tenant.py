from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Enum, String, Text
from sqlalchemy.orm import relationship

from backend.core.utils import generate_public_id
from backend.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPKMixin
from backend.models.enums import RerankerStrategy


class Tenant(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name = Column(String(255), nullable=False)
    public_id = Column(
        String(21),
        unique=True,
        nullable=False,
        index=True,
        default=generate_public_id,
    )
    openai_api_key = Column(String(500), nullable=True, default=None)
    settings = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    reranker_strategy = Column(
        Enum(RerankerStrategy, native_enum=False, length=16),
        nullable=False,
        default=RerankerStrategy.heuristic,
        server_default=RerankerStrategy.heuristic.value,
    )
    # LLM-provider alert state. Set when the chat pipeline hits an actionable
    # OpenAI failure (quota_exhausted, invalid_api_key); cleared on next
    # successful turn. Drives the dashboard banner and throttles the
    # "your key is broken" email to once per 24h. Stores the
    # backend.chat.llm_unavailable.LlmFailureType value as a plain string
    # to keep the column flexible (no DB enum).
    llm_alert_type = Column(String(64), nullable=True)
    llm_alert_first_at = Column(DateTime, nullable=True)
    llm_alert_last_email_at = Column(DateTime, nullable=True)

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

class Bot(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "bots"

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
    link_safety_enabled = Column(Boolean, nullable=False, default=False)
    allowed_domains = Column(JSON, nullable=True, default=list)
    # Tenant's own text, appended after the code preset.
    custom_instructions = Column(Text, nullable=True)
    # NULL means the tenant's own prompt replaces the preset entirely.
    preset = Column(String(64), nullable=True, server_default="support_agent")

    tenant = relationship("Tenant", back_populates="bots")
