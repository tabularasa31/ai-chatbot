"""add public_id to clients

Revision ID: add_public_id
Revises: 53879a65961c
Create Date: 2026-03-19

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "add_public_id"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("public_id", sa.String(20), nullable=True))

    conn = op.get_bind()
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
        existing_type=sa.String(20),
        nullable=False,
    )
    op.create_unique_constraint("uq_clients_public_id", "clients", ["public_id"])


def downgrade() -> None:
    op.drop_constraint("uq_clients_public_id", "clients", type_="unique")
    op.drop_column("clients", "public_id")
