"""Introduce background_jobs table for ARQ job-queue infrastructure.

ARQ persists job state in Redis, but Redis is volatile and not joinable
with app data. This Postgres table mirrors job lifecycle (queued →
in_progress → completed/failed/dead_letter) for admin UI and debugging.

Idempotent: each step inspects live state before acting.

Revision ID: arq_background_jobs_v1
Revises: drop_legacy_eval_tables_v1
Create Date: 2026-05-01
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

revision = "arq_background_jobs_v1"
down_revision = "drop_legacy_eval_tables_v1"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_index(insp, table: str, name: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(ix["name"] == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    dialect = bind.dialect.name

    if not _has_table(insp, "background_jobs"):
        if dialect == "postgresql":
            op.execute(
                text(
                    """
                    CREATE TABLE background_jobs (
                        id UUID PRIMARY KEY,
                        arq_job_id VARCHAR(64) NOT NULL,
                        kind VARCHAR(64) NOT NULL,
                        tenant_id UUID NULL REFERENCES tenants(id) ON DELETE CASCADE,
                        payload JSON NOT NULL DEFAULT '{}'::json,
                        status VARCHAR(16) NOT NULL DEFAULT 'queued',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 5,
                        last_error TEXT NULL,
                        last_error_at TIMESTAMP NULL,
                        created_at TIMESTAMP NOT NULL,
                        started_at TIMESTAMP NULL,
                        finished_at TIMESTAMP NULL
                    )
                    """
                )
            )
        else:
            op.execute(
                text(
                    """
                    CREATE TABLE background_jobs (
                        id CHAR(36) PRIMARY KEY,
                        arq_job_id VARCHAR(64) NOT NULL,
                        kind VARCHAR(64) NOT NULL,
                        tenant_id CHAR(36) NULL,
                        payload JSON NOT NULL DEFAULT '{}',
                        status VARCHAR(16) NOT NULL DEFAULT 'queued',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 5,
                        last_error TEXT NULL,
                        last_error_at DATETIME NULL,
                        created_at DATETIME NOT NULL,
                        started_at DATETIME NULL,
                        finished_at DATETIME NULL,
                        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                    )
                    """
                )
            )

    if not _has_index(insp, "background_jobs", "ix_background_jobs_arq_job_id"):
        op.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_background_jobs_arq_job_id "
                "ON background_jobs (arq_job_id)"
            )
        )
    if not _has_index(insp, "background_jobs", "ix_background_jobs_status"):
        op.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_background_jobs_status "
                "ON background_jobs (status)"
            )
        )
    if not _has_index(insp, "background_jobs", "ix_background_jobs_kind"):
        op.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_background_jobs_kind "
                "ON background_jobs (kind)"
            )
        )
    if not _has_index(insp, "background_jobs", "ix_background_jobs_tenant_id"):
        op.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_background_jobs_tenant_id "
                "ON background_jobs (tenant_id)"
            )
        )
    if not _has_index(insp, "background_jobs", "ix_background_jobs_created_at_desc"):
        op.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_background_jobs_created_at_desc "
                "ON background_jobs (created_at DESC)"
            )
        )


def downgrade() -> None:
    # Documented no-op: dropping the table would lose job history.
    pass
