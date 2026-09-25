"""Drop tenant_api_keys.

The widget API-key rotation feature (tenant_api_keys_v1) has been removed:
the model, service, and routes are gone from the codebase as of the
"delete tenant API-key feature" commit on this branch. Nothing reads or
writes this table anymore, so the table itself — including the backfilled
``created_by_label`` column added by ``member_removal_signatures_v1`` — is
dropped. Owner-approved deletion of the table and its data (2026-09-25).

No RLS policy exists for this table (it was one of the auth-boundary tables
listed as RLS-exempt in AGENTS.md), so there is nothing to drop there.

Idempotent: every step inspects live state before acting, so a replay after
a partial failure (or a second `alembic upgrade head` run) is a no-op.

Revision ID: drop_tenant_api_keys_v1
Revises: checklist_before_handoff_v1
Create Date: 2026-09-25 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "drop_tenant_api_keys_v1"
down_revision = "checklist_before_handoff_v1"
branch_labels = None
depends_on = None

_TABLE = "tenant_api_keys"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if _TABLE not in insp.get_table_names():
        return

    op.drop_table(_TABLE)


def downgrade() -> None:
    """Recreate the table structure only. Data (keys, hashes, labels) is
    NOT restored — it is gone once upgrade() runs. Structure mirrors
    tenant_api_keys_v1 plus the created_by_label column added later by
    member_removal_signatures_v1.
    """
    bind = op.get_bind()
    insp = sa_inspect(bind)
    dialect = bind.dialect.name

    if _TABLE in insp.get_table_names():
        return

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE tenant_api_keys (
                id UUID PRIMARY KEY,
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                key_hash VARCHAR(64) NOT NULL UNIQUE,
                key_hint VARCHAR(8) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                created_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NULL,
                revoked_at TIMESTAMP NULL,
                revoked_reason VARCHAR(32) NULL,
                last_used_at TIMESTAMP NULL,
                created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
                created_by_label VARCHAR(320) NULL
            )
            """
        )
        op.execute(
            "CREATE INDEX ix_tenant_api_keys_tenant_id ON tenant_api_keys (tenant_id)"
        )
        op.execute(
            "CREATE INDEX ix_tenant_api_keys_key_hash ON tenant_api_keys (key_hash)"
        )
        op.execute(
            "CREATE INDEX ix_tenant_api_keys_tenant_status "
            "ON tenant_api_keys (tenant_id, status)"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_api_keys_one_active "
            "ON tenant_api_keys (tenant_id) WHERE status = 'active'"
        )
    else:
        op.execute(
            """
            CREATE TABLE tenant_api_keys (
                id CHAR(36) PRIMARY KEY,
                tenant_id CHAR(36) NOT NULL,
                key_hash VARCHAR(64) NOT NULL UNIQUE,
                key_hint VARCHAR(8) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                created_at DATETIME NOT NULL,
                expires_at DATETIME NULL,
                revoked_at DATETIME NULL,
                revoked_reason VARCHAR(32) NULL,
                last_used_at DATETIME NULL,
                created_by_user_id CHAR(36) NULL,
                created_by_label VARCHAR(320) NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
                FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        op.execute(
            "CREATE INDEX ix_tenant_api_keys_tenant_id ON tenant_api_keys (tenant_id)"
        )
        op.execute(
            "CREATE INDEX ix_tenant_api_keys_key_hash ON tenant_api_keys (key_hash)"
        )
        op.execute(
            "CREATE INDEX ix_tenant_api_keys_tenant_status "
            "ON tenant_api_keys (tenant_id, status)"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_api_keys_one_active "
            "ON tenant_api_keys (tenant_id) WHERE status = 'active'"
        )
