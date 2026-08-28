from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base, _utcnow


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
    # Operator seat: NULL means no seat, a timestamp means one was granted
    # then. The seat is what lets a person *operate* — the console, replies
    # that land in the chat thread, the knowledge loop — where ``role``
    # governs only what they may *administer*. Inviting someone grants their
    # seat, and removing them (which deletes the row) releases it, so an
    # invited member is always seated. The one account that can sit here with
    # NULL is a workspace's own founding owner, who administers without a seat
    # and takes one only to answer from the console themselves.
    seat_granted_at = Column(DateTime, nullable=True)
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
