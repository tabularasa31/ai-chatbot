"""move_disclosure_config_tenant_to_bot

Revision ID: 47353c805cec
Revises: c89d2f4a1b3e
Create Date: 2026-04-19 13:22:39.077850

"""
from __future__ import annotations

import json
import secrets

import sqlalchemy as sa
from alembic import op

_PUBLIC_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


def _generate_public_id() -> str:
    return "".join(secrets.choice(_PUBLIC_ID_ALPHABET) for _ in range(21))


# revision identifiers, used by Alembic.
revision = '47353c805cec'
down_revision = 'c89d2f4a1b3e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bots", sa.Column("disclosure_config", sa.JSON(), nullable=True))

    conn = op.get_bind()

    # Ensure every tenant has at least one bot before we copy disclosure_config.
    # Tenants created after c89d2f4a1b3e but before this migration (i.e. before
    # create_tenant started auto-creating bots) may have no bot rows yet.
    bot_less = conn.execute(
        sa.text(
            "SELECT id, name FROM tenants"
            " WHERE id NOT IN (SELECT DISTINCT tenant_id FROM bots)"
        )
    ).fetchall()
    for tenant_id, tenant_name in bot_less:
        import uuid as _uuid
        conn.execute(
            sa.text(
                "INSERT INTO bots (id, tenant_id, name, public_id, is_active)"
                " VALUES (:id, :tenant_id, :name, :public_id, :is_active)"
            ),
            {
                "id": str(_uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "name": tenant_name,
                "public_id": _generate_public_id(),
                "is_active": True,
            },
        )

    _BATCH = 500
    offset = 0
    while True:
        rows = conn.execute(
            sa.text(
                "SELECT id, disclosure_config FROM tenants"
                " WHERE disclosure_config IS NOT NULL"
                " LIMIT :limit OFFSET :offset"
            ),
            {"limit": _BATCH, "offset": offset},
        ).fetchall()
        if not rows:
            break
        for tenant_id, raw in rows:
            try:
                cfg = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                continue
            if not isinstance(cfg, dict):
                continue
            conn.execute(
                sa.text(
                    "UPDATE bots SET disclosure_config = :cfg"
                    " WHERE tenant_id = :tid AND disclosure_config IS NULL"
                ),
                {"cfg": json.dumps(cfg), "tid": str(tenant_id)},
            )
        offset += _BATCH

    op.drop_column("tenants", "disclosure_config")


def downgrade() -> None:
    op.add_column("tenants", sa.Column("disclosure_config", sa.JSON(), nullable=True))

    conn = op.get_bind()
    # ORDER BY created_at ASC so we pick the oldest bot's config per tenant.
    # Python deduplication keeps it portable across PostgreSQL and SQLite.
    all_rows = conn.execute(
        sa.text(
            "SELECT tenant_id, disclosure_config FROM bots"
            " WHERE disclosure_config IS NOT NULL"
            " ORDER BY created_at ASC"
        )
    ).fetchall()
    seen: set = set()
    for tenant_id, raw in all_rows:
        if tenant_id in seen:
            continue
        seen.add(tenant_id)
        try:
            cfg = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if not isinstance(cfg, dict):
            continue
        conn.execute(
            sa.text("UPDATE tenants SET disclosure_config = :cfg WHERE id = :tid"),
            {"cfg": json.dumps(cfg), "tid": str(tenant_id)},
        )

    op.drop_column("bots", "disclosure_config")
