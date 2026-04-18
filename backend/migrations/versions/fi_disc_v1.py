"""Add clients.disclosure_config for tenant-wide response detail level (FI-DISC).

Revision ID: fi_disc_v1
Revises: fi_kyc_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fi_disc_v1"
down_revision = "fi_kyc_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("disclosure_config", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
