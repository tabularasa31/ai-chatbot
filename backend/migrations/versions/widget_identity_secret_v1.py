"""Add per-tenant widget identity signing secret

``tenants.widget_identity_secret`` holds an optional secret used to HMAC-sign
widget visitor user_ids, encrypted at rest the same way as
``tenants.openai_api_key``. Nullable: tenants without a secret keep today's
unsigned visitor identity.

Idempotent: inspects live state before acting.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "widget_identity_secret_v1"
down_revision = "drop_tenant_api_keys_v1"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    insp = sa_inspect(op.get_bind())
    if not _has_column(insp, "tenants", "widget_identity_secret"):
        op.add_column(
            "tenants",
            sa.Column("widget_identity_secret", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    # Documented for completeness; never run against shared/production DBs
    # (see global CLAUDE.md). Drops the encrypted secret column and its data.
    insp = sa_inspect(op.get_bind())
    if _has_column(insp, "tenants", "widget_identity_secret"):
        op.drop_column("tenants", "widget_identity_secret")
