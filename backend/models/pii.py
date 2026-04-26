from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.models.base import Base, _utcnow
from backend.models.enums import PiiEventDirection


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
