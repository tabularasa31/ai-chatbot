"""Live operator handoff, phase 0 — conversation state and message authorship.

Adds the columns the muting handler and the operator API need:

* ``chats.operator_state`` — ``bot`` | ``live``. Not null, defaults to ``bot``
  so every existing chat keeps today's behaviour. "Waiting for an operator" is
  derived (open ticket + no assignee), never stored.
* ``chats.assigned_operator_id`` — who claimed the conversation in the console.
* ``chats.operator_joined_at`` / ``chats.operator_released_at`` — handoff stamps.
* ``messages.operator_user_id`` — author of a ``MessageRole.operator`` row when
  it resolves to a tenant user; NULL when unattributed.

``messages.role`` is deliberately NOT altered. It was created as
``sa.Enum('user', 'assistant', name='messagerole', native_enum=False)`` in
``3e6c7b506784_init`` — a plain ``VARCHAR(9)`` with no CHECK constraint (SQLAlchemy
2.0 defaults ``create_constraint=False``, and no migration has added one since).
``'operator'`` is 8 characters, so the new enum value fits the existing column.

Both FKs are ``ON DELETE SET NULL``: deleting a user must never cascade into
conversation history.

Revision ID: operator_handoff_phase0_v1
Revises: pii_drop_redacted_columns_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "operator_handoff_phase0_v1"
down_revision = "pii_drop_redacted_columns_v1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    try:
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    except Exception:
        return False
    return column in cols


def _has_index(table: str, name: str) -> bool:
    if op.get_context().as_sql:
        return False
    bind = op.get_bind()
    try:
        return name in {i["name"] for i in sa.inspect(bind).get_indexes(table)}
    except Exception:
        return False


def upgrade() -> None:
    if not _has_column("chats", "operator_state"):
        op.add_column(
            "chats",
            sa.Column(
                "operator_state",
                sa.String(length=16),
                nullable=False,
                server_default="bot",
            ),
        )
    if not _has_column("chats", "assigned_operator_id"):
        op.add_column(
            "chats",
            sa.Column("assigned_operator_id", sa.UUID(), nullable=True),
        )
        op.create_foreign_key(
            "fk_chats_assigned_operator_id_users",
            "chats",
            "users",
            ["assigned_operator_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_index("chats", "ix_chats_assigned_operator_id"):
        op.create_index(
            "ix_chats_assigned_operator_id",
            "chats",
            ["assigned_operator_id"],
            unique=False,
        )
    if not _has_column("chats", "operator_joined_at"):
        op.add_column("chats", sa.Column("operator_joined_at", sa.DateTime(), nullable=True))
    if not _has_column("chats", "operator_released_at"):
        op.add_column("chats", sa.Column("operator_released_at", sa.DateTime(), nullable=True))

    # Partial index over the rare ``live`` state — mirrors ix_chats_sweeper_pending.
    if not _has_index("chats", "ix_chats_operator_live"):
        op.create_index(
            "ix_chats_operator_live",
            "chats",
            ["tenant_id", "updated_at"],
            unique=False,
            postgresql_where=sa.text("operator_state = 'live'"),
            sqlite_where=sa.text("operator_state = 'live'"),
        )

    if not _has_column("messages", "operator_user_id"):
        op.add_column(
            "messages",
            sa.Column("operator_user_id", sa.UUID(), nullable=True),
        )
        op.create_foreign_key(
            "fk_messages_operator_user_id_users",
            "messages",
            "users",
            ["operator_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Documentation only — downgrade is never executed against a shared or
    # production database (see project CLAUDE.md). Dropping ``operator_state``
    # would silently un-mute the bot in conversations a human is holding, and
    # dropping ``messages.operator_user_id`` would erase the authorship of
    # every human reply while leaving the rows themselves behind.
    op.drop_column("messages", "operator_user_id")
    op.drop_index("ix_chats_operator_live", table_name="chats")
    op.drop_index("ix_chats_assigned_operator_id", table_name="chats")
    op.drop_column("chats", "operator_released_at")
    op.drop_column("chats", "operator_joined_at")
    op.drop_column("chats", "assigned_operator_id")
    op.drop_column("chats", "operator_state")
