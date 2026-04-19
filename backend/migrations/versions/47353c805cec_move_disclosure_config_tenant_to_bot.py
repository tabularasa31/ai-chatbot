"""move_disclosure_config_tenant_to_bot

Revision ID: 47353c805cec
Revises: c89d2f4a1b3e
Create Date: 2026-04-19 13:22:39.077850

"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '47353c805cec'
down_revision = 'c89d2f4a1b3e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bots", sa.Column("disclosure_config", sa.JSON(), nullable=True))

    conn = op.get_bind()
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
    rows = conn.execute(
        sa.text(
            "SELECT DISTINCT ON (tenant_id) tenant_id, disclosure_config"
            " FROM bots"
            " WHERE disclosure_config IS NOT NULL"
            " ORDER BY tenant_id, created_at ASC"
        )
    ).fetchall()
    for tenant_id, raw in rows:
        try:
            cfg = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        conn.execute(
            sa.text("UPDATE tenants SET disclosure_config = :cfg WHERE id = :tid"),
            {"cfg": json.dumps(cfg), "tid": str(tenant_id)},
        )

    op.drop_column("bots", "disclosure_config")
