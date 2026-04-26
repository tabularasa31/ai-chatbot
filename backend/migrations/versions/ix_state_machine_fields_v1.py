"""Add indexes on status/key fields for UrlSourceRun and QuickAnswer state machines

Revision ID: ix_state_machine_fields_v1
Revises: lang_lock_v1
Create Date: 2026-04-26
"""

from alembic import op

revision = "ix_state_machine_fields_v1"
down_revision = "lang_lock_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_url_source_runs_status", "url_source_runs", ["status"])
    op.create_index("ix_quick_answers_key", "quick_answers", ["key"])


def downgrade() -> None:
    op.drop_index("ix_quick_answers_key", table_name="quick_answers")
    op.drop_index("ix_url_source_runs_status", table_name="url_source_runs")
