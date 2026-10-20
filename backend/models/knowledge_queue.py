"""Knowledge "Unanswered" queue: bot replies that left a visitor without an answer.

``UnansweredQuestion`` is one captured turn; ``UnansweredGroup`` clusters
questions that ask the same thing and carries the lifecycle (open, answered,
dismissed) plus the draft FAQ the tenant edits before publishing.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.models.base import Base, TenantScopedMixin, UUIDPKMixin, _utcnow
from backend.models.enums import (
    UnansweredDismissReason,
    UnansweredGroupStatus,
    UnansweredReason,
)


class UnansweredGroup(UUIDPKMixin, Base):
    __tablename__ = "unanswered_groups"
    __table_args__ = (
        Index("ix_unanswered_groups_tenant_last_asked", "tenant_id", "last_asked_at"),
    )

    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(
        Enum(UnansweredGroupStatus, native_enum=False, length=16),
        nullable=False,
        default=UnansweredGroupStatus.open,
        server_default=UnansweredGroupStatus.open.value,
    )
    dismiss_reason = Column(
        Enum(UnansweredDismissReason, native_enum=False, length=32),
        nullable=True,
    )
    status_changed_at = Column(DateTime, nullable=False, default=_utcnow)
    reopened_after_answer = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # SET NULL: deleting the published FAQ must not delete the group's history.
    faq_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_faq.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    draft_question = Column(Text, nullable=True)
    draft_answer = Column(Text, nullable=True)
    draft_language = Column(String(32), nullable=True)
    draft_version = Column(Integer, nullable=False, default=0, server_default="0")
    draft_generating_since = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, nullable=False, default=_utcnow)
    last_asked_at = Column(DateTime, nullable=False, default=_utcnow)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class UnansweredQuestion(UUIDPKMixin, TenantScopedMixin, Base):
    __tablename__ = "unanswered_questions"

    group_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("unanswered_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_message_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assistant_message_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    reason = Column(
        Enum(UnansweredReason, native_enum=False, length=32),
        nullable=False,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
