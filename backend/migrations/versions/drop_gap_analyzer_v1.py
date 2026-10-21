"""Remove the Gap Analyzer's data.

* Deletes the FAQ rows the feature published (``tenant_faq.source = 'gap_analyzer'``).
* Drops ``tenant_faq.gap_source_id`` (with its FK and unique partial index),
  ``cluster_size`` and ``source_message_ids``.
* Drops the ``gap_unified`` view and the six ``gap_*`` tables in one statement,
  because ``gap_clusters`` and ``gap_doc_topics`` reference each other. Their
  enum columns are VARCHAR (non-native), so there are no DB enum types to drop.

Revision ID: drop_gap_analyzer_v1
Revises: drop_log_analysis_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "drop_gap_analyzer_v1"
down_revision = "drop_log_analysis_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM tenant_faq WHERE source = 'gap_analyzer'")
    op.execute("DROP INDEX IF EXISTS uq_tenant_faq_gap_source_id")
    op.execute("ALTER TABLE tenant_faq DROP CONSTRAINT IF EXISTS fk_tenant_faq_gap_source_id")
    op.execute("ALTER TABLE tenant_faq DROP COLUMN IF EXISTS gap_source_id")
    op.execute("ALTER TABLE tenant_faq DROP COLUMN IF EXISTS cluster_size")
    op.execute("ALTER TABLE tenant_faq DROP COLUMN IF EXISTS source_message_ids")
    op.execute("DROP VIEW IF EXISTS gap_unified")
    op.execute(
        "DROP TABLE IF EXISTS gap_question_message_links, gap_questions, gap_dismissals, "
        "gap_clusters, gap_doc_topics, gap_analyzer_jobs"
    )


def downgrade() -> None:
    """Documentation only; never executed against a shared or production database.

    Restores the three nullable ``tenant_faq`` columns. The ``gap_*`` tables,
    the ``gap_unified`` view and the deleted FAQ rows are not restorable; use
    the earlier gap_* migrations to rebuild the empty structure.
    """
    op.add_column("tenant_faq", sa.Column("gap_source_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("tenant_faq", sa.Column("cluster_size", sa.Integer(), nullable=True))
    op.add_column("tenant_faq", sa.Column("source_message_ids", sa.JSON(), nullable=True))
