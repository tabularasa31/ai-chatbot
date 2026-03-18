"""add client_id to users

Revision ID: add_client_id
Revises: add_is_admin
Create Date: 2026-03-18

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "add_client_id"
down_revision = "add_is_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_users_client_id",
        "users",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_users_client_id"), "users", ["client_id"], unique=False)

    # Backfill: set client_id for users who own a client
    op.execute("""
        UPDATE users u
        SET client_id = (SELECT c.id FROM clients c WHERE c.user_id = u.id LIMIT 1)
        WHERE EXISTS (SELECT 1 FROM clients c WHERE c.user_id = u.id)
    """)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_client_id"), table_name="users")
    op.drop_constraint("fk_users_client_id", "users", type_="foreignkey")
    op.drop_column("users", "client_id")
