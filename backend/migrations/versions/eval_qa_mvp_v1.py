"""Internal eval QA tables (testers, eval_sessions, eval_results).

Revision ID: eval_qa_mvp_v1
Revises: a34b8c2d91f0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "eval_qa_mvp_v1"
down_revision = "a34b8c2d91f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "testers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_testers_username"), "testers", ["username"], unique=True)

    op.create_table(
        "eval_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bot_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tester_id"], ["testers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_eval_sessions_bot_id"), "eval_sessions", ["bot_id"], unique=False)
    op.create_index(op.f("ix_eval_sessions_tester_id"), "eval_sessions", ["tester_id"], unique=False)
    op.create_index(
        "ix_eval_sessions_tester_started",
        "eval_sessions",
        ["tester_id", "started_at"],
        unique=False,
    )

    op.create_table(
        "eval_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("bot_answer", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('pass', 'fail')",
            name="ck_eval_results_verdict",
        ),
        sa.CheckConstraint(
            "error_category IS NULL OR error_category IN ("
            "'hallucination', 'incomplete', 'wrong_generation', "
            "'off_topic', 'no_answer', 'other')",
            name="ck_eval_results_error_category",
        ),
        sa.CheckConstraint(
            "(verdict != 'pass' OR error_category IS NULL)",
            name="ck_eval_results_pass_no_category",
        ),
        sa.CheckConstraint(
            "(verdict != 'fail' OR error_category IS DISTINCT FROM 'other' OR "
            "(comment IS NOT NULL AND length(trim(comment)) > 0))",
            name="ck_eval_results_other_requires_comment",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["eval_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_eval_results_session_id"), "eval_results", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_eval_results_session_id"), table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_index("ix_eval_sessions_tester_started", table_name="eval_sessions")
    op.drop_index(op.f("ix_eval_sessions_tester_id"), table_name="eval_sessions")
    op.drop_index(op.f("ix_eval_sessions_bot_id"), table_name="eval_sessions")
    op.drop_table("eval_sessions")
    op.drop_index(op.f("ix_testers_username"), table_name="testers")
    op.drop_table("testers")
