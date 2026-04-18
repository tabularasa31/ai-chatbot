"""Gap Analyzer Phase 1 structural base.

Revision ID: gap_analyzer_phase1_v1
Revises: fi_clients_user_unique_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "gap_analyzer_phase1_v1"
down_revision = "fi_clients_user_unique_v1"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return getattr(bind.dialect, "name", "") == "postgresql"


def _create_gap_unified_view() -> None:
    op.execute(
        """
        CREATE VIEW gap_unified AS
        SELECT
            id,
            tenant_id,
            topic_label AS label,
            coverage_score,
            'mode_a' AS source,
            status,
            is_new,
            CAST(NULL AS FLOAT) AS aggregate_signal_weight,
            CAST(NULL AS INTEGER) AS question_count
        FROM gap_doc_topics
        UNION ALL
        SELECT
            id,
            tenant_id,
            label,
            coverage_score,
            'mode_b' AS source,
            status,
            is_new,
            aggregate_signal_weight,
            question_count
        FROM gap_clusters
        """
    )


def upgrade() -> None:
    is_postgres = _is_postgres()

    gap_source_enum = sa.Enum(
        "mode_a",
        "mode_b",
        name="gapsource",
        native_enum=False,
    )
    gap_cluster_status_enum = sa.Enum(
        "active",
        "dismissed",
        "closed",
        "inactive",
        name="gapclusterstatus",
        native_enum=False,
    )
    gap_doc_topic_status_enum = sa.Enum(
        "active",
        "closed",
        name="gapdoctopicstatus",
        native_enum=False,
    )
    gap_dismiss_reason_enum = sa.Enum(
        "feature_request",
        "not_relevant",
        "already_covered",
        "other",
        name="gapdismissreason",
        native_enum=False,
    )

    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "gap_clusters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "aggregate_signal_weight",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("coverage_score", sa.Float(), nullable=True),
        sa.Column(
            "status",
            gap_cluster_status_enum,
            nullable=False,
            server_default="active",
        ),
        sa.Column("linked_doc_topic_id", sa.UUID(), nullable=True),
        sa.Column("language_coverage", sa.JSON(), nullable=True),
        sa.Column("is_new", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("question_count_at_dismissal", sa.Integer(), nullable=True),
        sa.Column("last_computed_at", sa.DateTime(), nullable=True),
        sa.Column("last_question_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "gap_doc_topics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("topic_label", sa.Text(), nullable=True),
        sa.Column("coverage_score", sa.Float(), nullable=True),
        sa.Column(
            "status",
            gap_doc_topic_status_enum,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "example_questions",
            postgresql.ARRAY(sa.Text()) if is_postgres else sa.Text(),
            nullable=True,
        ),
        sa.Column("extraction_chunk_hash", sa.Text(), nullable=True),
        sa.Column("linked_cluster_id", sa.UUID(), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("is_new", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("extracted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "gap_questions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.UUID(), nullable=True),
        sa.Column(
            "gap_signal_weight",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("answer_confidence", sa.Float(), nullable=True),
        sa.Column("had_fallback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("had_escalation", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cluster_id"], ["gap_clusters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "gap_dismissals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source", gap_source_enum, nullable=False),
        sa.Column("gap_id", sa.UUID(), nullable=False),
        sa.Column("topic_label", sa.Text(), nullable=True),
        sa.Column("reason", gap_dismiss_reason_enum, nullable=False),
        sa.Column("dismissed_by", sa.UUID(), nullable=False),
        sa.Column(
            "dismissed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dismissed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "gap_question_message_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gap_question_id", sa.UUID(), nullable=False),
        sa.Column("user_message_id", sa.UUID(), nullable=False),
        sa.Column("assistant_message_id", sa.UUID(), nullable=False),
        sa.Column("chat_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["gap_question_id"], ["gap_questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    if is_postgres:
        op.execute("ALTER TABLE gap_clusters ADD COLUMN centroid vector(1536)")
        op.execute("ALTER TABLE gap_doc_topics ADD COLUMN topic_embedding vector(1536)")
        op.execute("ALTER TABLE gap_questions ADD COLUMN embedding vector(1536)")
        op.execute("ALTER TABLE gap_dismissals ADD COLUMN topic_label_embedding vector(1536)")
        op.create_foreign_key(
            "fk_gap_clusters_linked_doc_topic_id",
            "gap_clusters",
            "gap_doc_topics",
            ["linked_doc_topic_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            "fk_gap_doc_topics_linked_cluster_id",
            "gap_doc_topics",
            "gap_clusters",
            ["linked_cluster_id"],
            ["id"],
            ondelete="SET NULL",
        )
    else:
        op.add_column("gap_clusters", sa.Column("centroid", sa.Text(), nullable=True))
        op.add_column("gap_doc_topics", sa.Column("topic_embedding", sa.Text(), nullable=True))
        op.add_column("gap_questions", sa.Column("embedding", sa.Text(), nullable=True))
        op.add_column("gap_dismissals", sa.Column("topic_label_embedding", sa.Text(), nullable=True))

    op.create_index("ix_gap_clusters_tenant_status", "gap_clusters", ["tenant_id", "status"])
    op.create_index(
        "ix_gap_doc_topics_tenant_status",
        "gap_doc_topics",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_gap_questions_tenant_cluster",
        "gap_questions",
        ["tenant_id", "cluster_id"],
    )
    op.create_index(
        "ix_gap_dismissals_tenant_gap",
        "gap_dismissals",
        ["tenant_id", "source", "gap_id"],
    )
    op.create_index(
        "ix_gap_question_links_gap_question",
        "gap_question_message_links",
        ["gap_question_id"],
    )
    op.create_index(
        "ix_gap_question_links_user_message",
        "gap_question_message_links",
        ["user_message_id"],
    )
    op.create_index(
        "ix_gap_question_links_assistant_message",
        "gap_question_message_links",
        ["assistant_message_id"],
        unique=True,
    )
    op.create_index(
        "ix_gap_question_links_session_id",
        "gap_question_message_links",
        ["session_id"],
    )

    if is_postgres:
        op.execute(
            """
            CREATE INDEX ix_gap_questions_tenant_signal_weight
            ON gap_questions (tenant_id, gap_signal_weight DESC)
            """
        )
        op.execute(
            """
            CREATE INDEX ix_gap_clusters_tenant_signal_weight
            ON gap_clusters (tenant_id, aggregate_signal_weight DESC)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_gap_dismissals_topic_embedding_ivfflat
            ON gap_dismissals
            USING ivfflat (topic_label_embedding vector_cosine_ops)
            """
        )
    else:
        op.create_index(
            "ix_gap_questions_tenant_signal_weight",
            "gap_questions",
            ["tenant_id", "gap_signal_weight"],
        )
        op.create_index(
            "ix_gap_clusters_tenant_signal_weight",
            "gap_clusters",
            ["tenant_id", "aggregate_signal_weight"],
        )

    _create_gap_unified_view()


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
