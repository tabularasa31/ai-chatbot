"""add_bots_table

Revision ID: c89d2f4a1b3e
Revises: b75d0b64967e
Create Date: 2026-04-19

Changes:
  - Create `bots` table (id, tenant_id FK→tenants, name, public_id, is_active, timestamps)
  - Backfill one Bot per existing Tenant (name copied from tenant, fresh public_id)
"""

from __future__ import annotations

import secrets
import uuid

import sqlalchemy as sa
from alembic import op

revision = "c89d2f4a1b3e"
down_revision = "b75d0b64967e"
branch_labels = None
depends_on = None

_BOTS_TABLE = "bots"
_PUBLIC_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


def _generate_public_id() -> str:
    """21-char URL-safe random ID (same alphabet as nanoid default)."""
    return "".join(secrets.choice(_PUBLIC_ID_ALPHABET) for _ in range(21))


def upgrade() -> None:
    op.create_table(
        _BOTS_TABLE,
        # sa.UUID() is dialect-agnostic (PostgreSQL native UUID, SQLite VARCHAR)
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            sa.UUID(),
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
    # Omit created_at/updated_at — server_default handles them on both PG and SQLite.
    conn = op.get_bind()
    tenants = conn.execute(sa.text("SELECT id, name FROM tenants")).fetchall()
    for tenant_id, tenant_name in tenants:
        conn.execute(
            sa.text(
                "INSERT INTO bots (id, tenant_id, name, public_id, is_active)"
                " VALUES (:id, :tenant_id, :name, :public_id, :is_active)"
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
