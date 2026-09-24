from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, String

from backend.models.base import Base, TenantScopedMixin, UUIDPKMixin, _utcnow


class ContactSession(UUIDPKMixin, TenantScopedMixin, Base):
    """Cross-session history for identified users (v2+); v1 only persists schema."""

    __tablename__ = "contact_sessions"

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
