"""add public_id to clients

Revision ID: add_public_id
Revises: 53879a65961c
Create Date: 2026-03-19

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "add_public_id"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("public_id", sa.String(21), nullable=True))

    conn = op.get_bind()
    offline = op.get_context().as_sql

    if offline:
        # row_number() ordered by id is deterministically unique per row;
        # to_hex() padded to 18 chars fits String(21) and avoids the UUID-prefix
        # collision risk of the substr approach.
        op.execute(
            text(
                "UPDATE clients AS c "
                "SET public_id = 'ch_' || lpad(to_hex(sub.rn), 18, '0') "
                "FROM (SELECT id, row_number() OVER (ORDER BY id) AS rn "
                "      FROM clients WHERE public_id IS NULL) sub "
                "WHERE c.id = sub.id"
            )
        )
    else:
        from backend.core.utils import generate_public_id

        result = conn.execute(text("SELECT id FROM clients WHERE public_id IS NULL"))
        rows = result.fetchall()
        seen: set[str] = set()
        for (id_val,) in rows:
            while True:
                pid = generate_public_id()
                if pid not in seen:
                    seen.add(pid)
                    break
            conn.execute(
                text("UPDATE clients SET public_id = :pid WHERE id = :id"),
                {"pid": pid, "id": str(id_val)},
            )

    op.alter_column(
        "clients",
        "public_id",
        existing_type=sa.String(21),
        nullable=False,
    )
    op.create_unique_constraint("uq_clients_public_id", "clients", ["public_id"])


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
