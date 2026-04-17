"""gap_analyzer_indexes_v1.

Adds the missing Gap Analyzer indexes identified from the current hot-query
paths. ANN indexes are Postgres-only because SQLite stores vectors as text in
tests and local lightweight setups.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "gap_analyzer_indexes_v1"
down_revision = "lang_escalation_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"

    if is_postgres:
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_gap_clusters_centroid_ivfflat
            ON gap_clusters
            USING ivfflat (centroid vector_cosine_ops)
            WITH (lists = 100)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_gap_doc_topics_topic_embedding_ivfflat
            ON gap_doc_topics
            USING ivfflat (topic_embedding vector_cosine_ops)
            WITH (lists = 100)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_gap_questions_embedding_ivfflat
            ON gap_questions
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
            """
        )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gap_clusters_linked_doc_topic_id
        ON gap_clusters (linked_doc_topic_id)
        WHERE linked_doc_topic_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gap_doc_topics_linked_cluster_id
        ON gap_doc_topics (linked_cluster_id)
        WHERE linked_cluster_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gap_dismissals_dismissed_by
        ON gap_dismissals (dismissed_by)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gap_jobs_expired_lease_in_progress
        ON gap_analyzer_jobs (lease_expires_at)
        WHERE status = 'in_progress'
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_gap_jobs_lease_expires")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gap_doc_topics_tenant_extraction_hash
        ON gap_doc_topics (tenant_id, extraction_chunk_hash)
        WHERE extraction_chunk_hash IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gap_dismissals_tenant_dismissed_at
        ON gap_dismissals (tenant_id, dismissed_at DESC)
        """
    )


def downgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"

    op.execute("DROP INDEX IF EXISTS ix_gap_dismissals_tenant_dismissed_at")
    op.execute("DROP INDEX IF EXISTS ix_gap_doc_topics_tenant_extraction_hash")
    op.execute("DROP INDEX IF EXISTS ix_gap_jobs_expired_lease_in_progress")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_gap_jobs_lease_expires
        ON gap_analyzer_jobs (lease_expires_at)
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_gap_dismissals_dismissed_by")
    op.execute("DROP INDEX IF EXISTS ix_gap_doc_topics_linked_cluster_id")
    op.execute("DROP INDEX IF EXISTS ix_gap_clusters_linked_doc_topic_id")

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS ix_gap_questions_embedding_ivfflat")
        op.execute("DROP INDEX IF EXISTS ix_gap_doc_topics_topic_embedding_ivfflat")
        op.execute("DROP INDEX IF EXISTS ix_gap_clusters_centroid_ivfflat")
