from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, String, Text
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
