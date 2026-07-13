from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.models.base import Base, _utcnow


class GuardEvent(Base):
    """One guard verdict for one chat turn.

    Structured record of what the guards subsystem decided, so we can measure
    our own false-positive / false-negative rate instead of tuning blind. One
    row per guard invocation on the primary chat path (injection detector and
    relevance classifier). ``label`` is written later by the manual FP/FN
    review tool (separate ticket) — it stays NULL until a human marks the
    verdict as mistaken.

    ``evidence_hash`` stores a SHA-256 of the trigger (matched pattern / note),
    never the raw user text, so the table carries no message content.
    """

    __tablename__ = "guard_events"
    # Composite (tenant_id, created_at) serves the FP/FN dashboard's core
    # access pattern — a tenant's events over a time window — and its
    # tenant_id prefix covers plain tenant scans, so no separate single-column
    # index is needed on either.
    __table_args__ = (
        Index("ix_guard_events_tenant_created", "tenant_id", "created_at"),
    )

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Turn identifier. Nullable because the internal (non-chat) callers of the
    # injection guard — e.g. the Gap Analyzer vetting LLM-authored drafts — have
    # no chat context. SET NULL so purging a chat keeps the aggregate FP/FN
    # signal.
    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Which guard produced the verdict: 'injection' | 'relevance'. Low
    # cardinality — not worth a standalone index on this write-heavy table.
    kind = Column(String(32), nullable=False)
    blocked = Column(Boolean, nullable=False)
    # VerdictReason value (e.g. 'injection_semantic', 'offtopic', 'timeout').
    # Indexed: FP/FN analysis slices by reason category.
    reason = Column(String(48), nullable=False, index=True)
    score = Column(Float, nullable=True)
    evidence_hash = Column(String(64), nullable=True)
    latency_ms = Column(Float, nullable=True)
    cache_hit = Column(Boolean, nullable=True)
    # Manual FP/FN annotation, filled by the review tool: 'fp' | 'fn' | NULL.
    # Indexed: the review tool queries WHERE label IS NOT NULL (sparse).
    label = Column(String(16), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
