"""Widen escalation_tickets.trigger to VARCHAR(32).

``67aaa83e5689`` narrowed the column to VARCHAR(15) — the length of the
longest value that existed then. The new ``clarify_loop_limit`` trigger is 18
characters, so loop- and clarification-ceiling escalations could not be
recorded under their own reason without this. 32 rather than 18 for the same
reason ``chats.operator_state`` is explicit: a VARCHAR sized to today's enum
turns every new value into a column alteration.

On PostgreSQL widening a VARCHAR is metadata-only — no table rewrite, no lock
beyond the brief ACCESS EXCLUSIVE of the catalog update.

Historical rows are left alone: tickets that were caused by a loop or by the
clarification ceiling were written as ``low_similarity`` and their real reason
was never stored anywhere, so there is nothing to backfill from. Counts by
trigger are honest only from this revision forward.

Revision ID: esc_trigger_width_v1
Revises: answer_cache_v1
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "esc_trigger_width_v1"
down_revision = "answer_cache_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "escalation_tickets",
        "trigger",
        type_=sa.String(32),
        existing_type=sa.String(15),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Documentation only — never run. Narrowing back rewrites the table and
    # fails outright on any row holding ``clarify_loop_limit`` (18 chars).
    op.alter_column(
        "escalation_tickets",
        "trigger",
        type_=sa.String(15),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
