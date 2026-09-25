from __future__ import annotations

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.models.base import Base, TenantScopedMixin, UUIDPKMixin, _utcnow
from backend.models.enums import PiiEventDirection


class PiiEvent(UUIDPKMixin, TenantScopedMixin, Base):
    __tablename__ = "pii_events"

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
