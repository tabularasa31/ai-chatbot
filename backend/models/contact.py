from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.models.base import Base, _utcnow


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
