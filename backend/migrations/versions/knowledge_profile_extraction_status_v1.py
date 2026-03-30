"""Add extraction_status to tenant_profiles.

Revision ID: knowledge_profile_status_v1
Revises: add_tenant_profiles_and_faq
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "knowledge_profile_status_v1"
down_revision = "add_tenant_profiles_and_faq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_profiles",
        sa.Column(
            "extraction_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_profiles", "extraction_status")

