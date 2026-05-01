from __future__ import annotations

import enum
import uuid

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend.models.base import Base, _utcnow


class BackgroundJobStatus(str, enum.Enum):
    queued = "queued"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    dead_letter = "dead_letter"


class BackgroundJob(Base):
    """Status row mirroring an ARQ job, written by queue hooks.

    ARQ already persists job state in Redis, but Redis state is volatile and
    not joinable with the rest of the app's data. This row is the durable,
    Postgres-backed view used by admin UI and debugging.
    """

    __tablename__ = "background_jobs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    arq_job_id = Column(String(64), nullable=False, unique=True, index=True)
    kind = Column(String(64), nullable=False)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(
        String(16),
        nullable=False,
        default=BackgroundJobStatus.queued.value,
    )
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_background_jobs_status", "status"),
        Index("ix_background_jobs_kind", "kind"),
        Index("ix_background_jobs_tenant_id", "tenant_id"),
        Index("ix_background_jobs_created_at_desc", created_at.desc()),
    )
