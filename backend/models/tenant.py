from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from backend.core.utils import generate_public_id
from backend.models.base import Base, _utcnow


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
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
    api_keys = relationship(
        "TenantApiKey",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# Status values for TenantApiKey.status. Kept as plain strings (no Enum) to
# match existing patterns in this module.
TENANT_API_KEY_STATUS_ACTIVE = "active"
TENANT_API_KEY_STATUS_REVOKING = "revoking"
TENANT_API_KEY_STATUS_REVOKED = "revoked"

TENANT_API_KEY_REASONS = ("leaked", "scheduled", "compromise", "manual", "other")


class TenantApiKey(Base):
    __tablename__ = "tenant_api_keys"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 of the plaintext ck_… key. 64 hex chars.
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    # Last 4 chars of the plaintext key, displayed in the UI to identify a
    # rotated key without revealing its full value.
    key_hint = Column(String(8), nullable=False)
    status = Column(String(16), nullable=False, default=TENANT_API_KEY_STATUS_ACTIVE)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(32), nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    tenant = relationship("Tenant", back_populates="api_keys")

    __table_args__ = (
        Index("ix_tenant_api_keys_tenant_status", "tenant_id", "status"),
        # Partial unique index enforcing at most one ACTIVE row per tenant.
        # The actual DDL is created in the alembic migration; this annotation
        # keeps the constraint visible to anyone reading the model.
        Index(
            "uq_tenant_api_keys_one_active",
            "tenant_id",
            unique=True,
            postgresql_where=(status == TENANT_API_KEY_STATUS_ACTIVE),
            sqlite_where=(status == TENANT_API_KEY_STATUS_ACTIVE),
        ),
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
    link_safety_enabled = Column(Boolean, nullable=False, default=False)
    allowed_domains = Column(JSON, nullable=True, default=list)
    agent_instructions = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    tenant = relationship("Tenant", back_populates="bots")
