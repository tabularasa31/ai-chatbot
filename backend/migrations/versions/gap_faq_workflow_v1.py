"""Gap Analyzer → FAQ workflow: draft state on gap_clusters + gap_source_id on tenant_faq.

Adds the persistent draft state needed to take a Mode B gap cluster from
"detected" through "LLM-drafted, admin-reviewed, published" without losing
work between sessions. Also links a published FAQ row back to the gap it
came from so the FAQ tab can group "Resolutions from gaps".

Status values ``drafting``, ``in_review``, ``resolved`` are added to the
Python ``GapClusterStatus`` enum — the column is stored as VARCHAR (the
SQLAlchemy column uses ``native_enum=False``) so no ``ALTER TYPE`` is
required.

Revision ID: gap_faq_workflow_v1
Revises: escalation_pre_confirm_v1
Create Date: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "gap_faq_workflow_v1"
down_revision = "escalation_pre_confirm_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    gap_cluster_cols = {c["name"] for c in inspector.get_columns("gap_clusters")}
    if "draft_title" not in gap_cluster_cols:
        op.add_column("gap_clusters", sa.Column("draft_title", sa.Text(), nullable=True))
    if "draft_question" not in gap_cluster_cols:
        op.add_column("gap_clusters", sa.Column("draft_question", sa.Text(), nullable=True))
    if "draft_markdown" not in gap_cluster_cols:
        op.add_column("gap_clusters", sa.Column("draft_markdown", sa.Text(), nullable=True))
    if "draft_language" not in gap_cluster_cols:
        op.add_column("gap_clusters", sa.Column("draft_language", sa.String(length=8), nullable=True))
    if "draft_updated_at" not in gap_cluster_cols:
        op.add_column("gap_clusters", sa.Column("draft_updated_at", sa.DateTime(), nullable=True))
    if "published_faq_id" not in gap_cluster_cols:
        op.add_column(
            "gap_clusters",
            sa.Column("published_faq_id", PG_UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_gap_clusters_published_faq_id",
            "gap_clusters",
            "tenant_faq",
            ["published_faq_id"],
            ["id"],
            ondelete="SET NULL",
        )

    tenant_faq_cols = {c["name"] for c in inspector.get_columns("tenant_faq")}
    if "gap_source_id" not in tenant_faq_cols:
        op.add_column(
            "tenant_faq",
            sa.Column("gap_source_id", PG_UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_tenant_faq_gap_source_id",
            "tenant_faq",
            "gap_clusters",
            ["gap_source_id"],
            ["id"],
            ondelete="SET NULL",
        )

    tenant_faq_indexes = {i["name"] for i in inspector.get_indexes("tenant_faq")}
    if "ix_tenant_faq_gap_source_id" not in tenant_faq_indexes:
        op.create_index(
            "ix_tenant_faq_gap_source_id",
            "tenant_faq",
            ["gap_source_id"],
        )
    # Concurrency guard: at most one published FAQ per gap cluster. Two
    # racing POST /publish requests can both pass the orchestrator's status
    # check; the unique partial index makes the second INSERT fail at the DB
    # layer (caught by the route and translated to 409).
    if "uq_tenant_faq_gap_source_id" not in tenant_faq_indexes:
        op.create_index(
            "uq_tenant_faq_gap_source_id",
            "tenant_faq",
            ["gap_source_id"],
            unique=True,
            postgresql_where=sa.text("gap_source_id IS NOT NULL"),
            sqlite_where=sa.text("gap_source_id IS NOT NULL"),
        )


def downgrade() -> None:
    # Documented no-op per project Alembic policy: never drop columns that may
    # hold real data. Re-running upgrade is idempotent via the inspector guards.
    pass
