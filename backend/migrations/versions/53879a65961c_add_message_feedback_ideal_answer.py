"""add_message_feedback_ideal_answer

Revision ID: 53879a65961c
Revises: 48eb257a6a0a
Create Date: 2026-03-18 16:25:09.062646

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '53879a65961c'
down_revision = '48eb257a6a0a'
branch_labels = None
depends_on = None


def _drop_legacy_feedback_check_constraint(*, dialect: str, offline: bool) -> None:
    if dialect != "postgresql":
        return
    if offline:
        op.execute(
            """
            DO $$
            DECLARE constraint_name text;
            BEGIN
                SELECT conname INTO constraint_name
                FROM pg_constraint
                WHERE conrelid = 'messages'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%positive%'
                LIMIT 1;

                IF constraint_name IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE messages DROP CONSTRAINT '
                        || quote_ident(constraint_name);
                END IF;
            END $$;
            """
        )
        return

    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'messages'::regclass AND contype = 'c' "
            "AND pg_get_constraintdef(oid) LIKE '%positive%'"
        )
    )
    for row in result:
        op.execute(sa.text(f"ALTER TABLE messages DROP CONSTRAINT {row[0]}"))


def upgrade() -> None:
    op.add_column("messages", sa.Column("ideal_answer", sa.Text(), nullable=True))

    conn = op.get_bind()
    dialect = conn.dialect.name
    offline = op.get_context().as_sql
    # With native_enum=False, both SQLite and PostgreSQL use VARCHAR for feedback.
    # Drop CHECK constraint if present (PostgreSQL), then update values.
    _drop_legacy_feedback_check_constraint(dialect=dialect, offline=offline)
    conn.execute(
        sa.text(
            "UPDATE messages SET feedback = CASE feedback "
            "WHEN 'positive' THEN 'up' WHEN 'negative' THEN 'down' ELSE feedback END"
        )
    )
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE messages ADD CONSTRAINT messages_feedback_check "
            "CHECK (feedback IN ('none', 'up', 'down'))"
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    offline = op.get_context().as_sql
    if dialect == "postgresql":
        op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_feedback_check")
    conn.execute(
        sa.text(
            "UPDATE messages SET feedback = CASE feedback "
            "WHEN 'up' THEN 'positive' WHEN 'down' THEN 'negative' ELSE feedback END"
        )
    )
    if dialect == "postgresql":
        _drop_legacy_feedback_check_constraint(dialect=dialect, offline=offline)
        op.execute(
            "ALTER TABLE messages ADD CONSTRAINT messages_feedback_check "
            "CHECK (feedback IN ('none', 'positive', 'negative'))"
        )
    op.drop_column("messages", "ideal_answer")
