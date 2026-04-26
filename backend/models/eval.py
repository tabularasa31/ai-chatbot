from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base, _utcnow


class Tester(Base):
    """Internal QA tester (plain password, MVP only)."""

    __tablename__ = "testers"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    username = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    sessions = relationship(
        "EvalSession",
        back_populates="tester",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EvalSession(Base):
    __tablename__ = "eval_sessions"

    __table_args__ = (
        Index("ix_eval_sessions_tester_started", "tester_id", "started_at"),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tester_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("testers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bot_id = Column(String(64), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=_utcnow)

    tester = relationship("Tester", back_populates="sessions")
    results = relationship(
        "EvalResult",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EvalResult(Base):
    __tablename__ = "eval_results"

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('pass', 'fail')",
            name="ck_eval_results_verdict",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN ("
            "'hallucination', 'incomplete', 'wrong_generation', "
            "'off_topic', 'no_answer', 'other')",
            name="ck_eval_results_error_category",
        ),
        CheckConstraint(
            "(verdict != 'pass' OR error_category IS NULL)",
            name="ck_eval_results_pass_no_category",
        ),
        CheckConstraint(
            "(verdict != 'fail' OR error_category IS DISTINCT FROM 'other' OR "
            "(comment IS NOT NULL AND length(trim(comment)) > 0))",
            name="ck_eval_results_other_requires_comment",
        ),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question = Column(Text, nullable=False)
    bot_answer = Column(Text, nullable=False)
    verdict = Column(String(16), nullable=False)
    error_category = Column(String(32), nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    session = relationship("EvalSession", back_populates="results")
