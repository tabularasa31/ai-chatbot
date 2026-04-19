"""member_role_contact_session

Revision ID: b75d0b64967e
Revises: 93e5ff7b6924
Create Date: 2026-04-19

Changes:
  - users: add `role` column (default 'owner', backfill all existing rows)
  - tenants: drop `user_id` FK column (ownership now via users.tenant_id + users.role)
  - user_sessions: rename table → contact_sessions
  - contact_sessions: rename column user_id → contact_id
  - contact_sessions: rename indexes ix/uq_user_sessions_* → ix/uq_contact_sessions_*
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b75d0b64967e"
down_revision = "93e5ff7b6924"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add role to users (backfill all existing as 'owner')
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(32), nullable=False, server_default="owner")
        )

    # 2. Backfill users.tenant_id from tenants.user_id before dropping the source column.
    # On PostgreSQL, USE the FROM clause; SQLite doesn't support FROM in UPDATE so
    # we use a correlated subquery that works on both dialects.
    op.execute(
        """
        UPDATE users
        SET tenant_id = (
            SELECT tenants.id
            FROM tenants
            WHERE tenants.user_id = users.id
        )
        WHERE users.tenant_id IS NULL
          AND EXISTS (
            SELECT 1 FROM tenants WHERE tenants.user_id = users.id
          )
        """
    )

    # 3. Drop tenants.user_id FK + column.
    # batch_alter_table recreates the table (SQLite) or uses ALTER (PG),
    # so we don't need to name the FK — batch_op drops it with the column.
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("user_id")

    # 4. Rename table user_sessions → contact_sessions
    op.rename_table("user_sessions", "contact_sessions")

    # 5. Rename column user_id → contact_id; rename indexes
    with op.batch_alter_table("contact_sessions") as batch_op:
        batch_op.alter_column("user_id", new_column_name="contact_id")
        batch_op.drop_index("ix_user_sessions_tenant_user")
        batch_op.drop_index("uq_user_sessions_tenant_user_active")
        batch_op.create_index(
            "ix_contact_sessions_tenant_contact",
            ["tenant_id", "contact_id"],
        )
        batch_op.create_index(
            "uq_contact_sessions_tenant_contact_active",
            ["tenant_id", "contact_id"],
            unique=True,
            postgresql_where="session_ended_at IS NULL",
            sqlite_where="session_ended_at IS NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("contact_sessions") as batch_op:
        batch_op.drop_index("uq_contact_sessions_tenant_contact_active")
        batch_op.drop_index("ix_contact_sessions_tenant_contact")
        batch_op.create_index(
            "ix_user_sessions_tenant_user", ["tenant_id", "contact_id"]
        )
        batch_op.create_index(
            "uq_user_sessions_tenant_user_active",
            ["tenant_id", "contact_id"],
            unique=True,
            postgresql_where="session_ended_at IS NULL",
            sqlite_where="session_ended_at IS NULL",
        )
        batch_op.alter_column("contact_id", new_column_name="user_id")

    op.rename_table("contact_sessions", "user_sessions")

    # Restore tenants.user_id — initially nullable so we can backfill.
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(
            sa.Column(
                "user_id",
                sa.UUID(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_tenants_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    # Backfill tenants.user_id from the owner user (role = 'owner') before enforcing NOT NULL.
    op.execute(
        """
        UPDATE tenants
        SET user_id = (
            SELECT users.id
            FROM users
            WHERE users.tenant_id = tenants.id
              AND users.role = 'owner'
            LIMIT 1
        )
        WHERE user_id IS NULL
        """
    )

    # Now tighten to NOT NULL + UNIQUE to restore original schema constraints.
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.alter_column("user_id", nullable=False)
        batch_op.create_unique_constraint("uq_tenants_user_id", ["user_id"])

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("role")
