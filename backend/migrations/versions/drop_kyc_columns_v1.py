"""Drop legacy KYC columns from tenants (replaced by widget userHints).

Revision ID: drop_kyc_columns_v1
Revises: arq_background_jobs_v1
Create Date: 2026-05-05

"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "drop_kyc_columns_v1"
down_revision = "arq_background_jobs_v1"
branch_labels = None
depends_on = None


_KYC_COLUMNS = (
    "kyc_secret_key",
    "kyc_secret_key_previous",
    "kyc_secret_previous_expires_at",
    "kyc_secret_key_hint",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in inspect(bind).get_columns("tenants")}
    for column in _KYC_COLUMNS:
        if column in existing:
            op.drop_column("tenants", column)


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    raise NotImplementedError("downgrade is not supported for this migration")
