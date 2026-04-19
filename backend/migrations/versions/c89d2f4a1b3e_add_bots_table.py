"""add_bots_table

Revision ID: c89d2f4a1b3e
Revises: b75d0b64967e
Create Date: 2026-04-19

Changes:
  - Create `bots` table (id, tenant_id FK→tenants, name, public_id, is_active, timestamps)
  - Backfill one Bot per existing Tenant (name copied from tenant, fresh public_id)
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "c89d2f4a1b3e"
down_revision = "b75d0b64967e"
branch_labels = None
depends_on = None

_BOTS_TABLE = "bots"


def _generate_public_id() -> str:
    """21-char URL-safe random ID (same alphabet as nanoid default)."""
    import secrets
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    return "".join(secrets.choice(alphabet) for _ in range(21))


def upgrade() -> None:
    op.create_table(
        _BOTS_TABLE,
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("public_id", sa.String(21), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bots_tenant_id", _BOTS_TABLE, ["tenant_id"])
    op.create_index("ix_bots_public_id", _BOTS_TABLE, ["public_id"], unique=True)

    # Backfill: one default Bot per existing Tenant.
    conn = op.get_bind()
    tenants = conn.execute(sa.text("SELECT id, name FROM tenants")).fetchall()
    for tenant_id, tenant_name in tenants:
        conn.execute(
            sa.text(
                "INSERT INTO bots (id, tenant_id, name, public_id, is_active, created_at, updated_at)"
                " VALUES (:id, :tenant_id, :name, :public_id, :is_active, NOW(), NOW())"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "name": tenant_name,
                "public_id": _generate_public_id(),
                "is_active": True,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_bots_public_id", _BOTS_TABLE)
    op.drop_index("ix_bots_tenant_id", _BOTS_TABLE)
    op.drop_table(_BOTS_TABLE)
