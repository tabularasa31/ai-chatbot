"""Ensure users.is_admin column exists (repair drifted DBs).

Revision ID: repair_is_admin_v1
Revises: fi_esc_v1

If `add_is_admin` never applied to a database (or schema drifted), ORM loads of
User fail with 500 — browsers may report misleading CORS errors.

See cursor_prompts/RULES-database-migrations.md — upgrade-only in deployed envs;
downgrade() is intentionally a no-op (never run downgrade against prod).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "repair_is_admin_v1"
down_revision = "fi_esc_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")}
    if "is_admin" in cols:
        return
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
