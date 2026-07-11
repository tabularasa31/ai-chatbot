"""Backfill tenant_profiles for tenants created before eager-create.

Revision ID: tenant_profile_backfill_v1
Revises: chat_last_detected_language_v1

GET /knowledge/profile no longer lazy-creates TenantProfile; new tenants get
a profile row at create_tenant time. This backfills a default profile for any
existing tenant that never hit the old lazy-create path, so the route's 404
branch is unreachable for provisioned tenants.

Idempotent: only inserts rows for tenants without a profile.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tenant_profile_backfill_v1"
down_revision = "chat_last_detected_language_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO tenant_profiles
                (tenant_id, topics, glossary, aliases, support_urls,
                 extraction_status, updated_at)
            SELECT t.id, '[]', '[]', '[]', '[]', 'pending', CURRENT_TIMESTAMP
            FROM tenants t
            WHERE NOT EXISTS (
                SELECT 1 FROM tenant_profiles p WHERE p.tenant_id = t.id
            )
            """
        )
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    raise NotImplementedError("downgrade is not supported for this migration")
