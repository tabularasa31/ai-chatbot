"""Enforce one client per user.

Revision ID: fi_clients_user_unique_v1
Revises: phase4_user_sessions_active_v1
"""

from __future__ import annotations

from alembic import op

revision = "fi_clients_user_unique_v1"
down_revision = "phase4_user_sessions_active_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM clients
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY user_id
                        ORDER BY created_at ASC, id ASC
                    ) AS row_num
                FROM clients
            ) ranked
            WHERE ranked.row_num > 1
        )
        """
    )
    op.execute(
        """
        UPDATE users
        SET client_id = (
            SELECT c.id
            FROM clients AS c
            WHERE c.user_id = users.id
            ORDER BY c.created_at ASC, c.id ASC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1
            FROM clients AS c
            WHERE c.user_id = users.id
              AND (users.client_id IS NULL OR users.client_id <> c.id)
        )
        """
    )
    op.create_unique_constraint("uq_clients_user_id", "clients", ["user_id"])


def downgrade() -> None:
    # no-op: downgrade is never executed (see project CLAUDE.md)
    pass
