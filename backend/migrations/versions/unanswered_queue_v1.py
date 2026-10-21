"""Knowledge "Unanswered" queue — storage layer.

* ``messages.escalation_offer_reason`` — ``EscalationTrigger`` value, set only
  on the assistant message that offers an escalation.
* ``users.knowledge_queue_seen_at`` — per-member "last opened the queue" stamp
  behind the new-items badge.
* ``unanswered_groups`` — questions asking the same thing, with lifecycle
  (open | answered | dismissed) and the draft FAQ being edited.
* ``unanswered_questions`` — one captured bot turn; ``assistant_message_id`` is
  unique so a turn is captured at most once.

String columns are VARCHAR(32) rather than sized to today's longest value, so
adding an enum value never needs a column alteration. RLS is applied in this
revision (frozen policy snapshot, deliberately not imported from
``backend/core/rls.py``).

Revision ID: unanswered_queue_v1
Revises: widget_identity_secret_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "unanswered_queue_v1"
down_revision = "widget_identity_secret_v1"
branch_labels = None
depends_on = None

_GROUPS = "unanswered_groups"
_QUESTIONS = "unanswered_questions"
_RLS_TABLES = (_GROUPS, _QUESTIONS)
_CTX = "NULLIF(current_setting('app.tenant_id', true), '')"


def _has_column(table: str, column: str) -> bool:
    if op.get_context().as_sql:
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _has_table(table: str) -> bool:
    if op.get_context().as_sql:
        return False
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _has_index(table: str, name: str) -> bool:
    if op.get_context().as_sql:
        return False
    return name in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    if not _has_column("messages", "escalation_offer_reason"):
        op.add_column(
            "messages",
            sa.Column("escalation_offer_reason", sa.String(length=32), nullable=True),
        )
    if not _has_column("users", "knowledge_queue_seen_at"):
        op.add_column(
            "users", sa.Column("knowledge_queue_seen_at", sa.DateTime(), nullable=True)
        )

    if not _has_table(_GROUPS):
        op.create_table(
            _GROUPS,
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column(
                "status", sa.String(length=16), nullable=False, server_default="open"
            ),
            sa.Column("dismiss_reason", sa.String(length=32), nullable=True),
            sa.Column("status_changed_at", sa.DateTime(), nullable=False),
            sa.Column(
                "reopened_after_answer",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
            sa.Column("faq_id", _uuid(), nullable=True),
            sa.Column("draft_question", sa.Text(), nullable=True),
            sa.Column("draft_answer", sa.Text(), nullable=True),
            sa.Column("draft_language", sa.String(length=32), nullable=True),
            sa.Column(
                "draft_version", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("draft_generating_since", sa.DateTime(), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_asked_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["faq_id"], ["tenant_faq.id"], ondelete="SET NULL"),
        )
    if not _has_index(_GROUPS, "ix_unanswered_groups_tenant_last_asked"):
        op.create_index(
            "ix_unanswered_groups_tenant_last_asked",
            _GROUPS,
            ["tenant_id", "last_asked_at"],
        )
    if not _has_index(_GROUPS, "ix_unanswered_groups_faq_id"):
        op.create_index("ix_unanswered_groups_faq_id", _GROUPS, ["faq_id"])

    if not _has_table(_QUESTIONS):
        op.create_table(
            _QUESTIONS,
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("group_id", _uuid(), nullable=False),
            sa.Column("chat_id", _uuid(), nullable=False),
            sa.Column("user_message_id", _uuid(), nullable=False),
            sa.Column("assistant_message_id", _uuid(), nullable=False),
            sa.Column("reason", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["group_id"], ["unanswered_groups.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["user_message_id"], ["messages.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["assistant_message_id"], ["messages.id"], ondelete="CASCADE"
            ),
        )
    if not _has_index(_QUESTIONS, "ix_unanswered_questions_tenant_created"):
        op.create_index(
            "ix_unanswered_questions_tenant_created",
            _QUESTIONS,
            ["tenant_id", "created_at"],
        )
    for column in ("group_id", "chat_id", "user_message_id"):
        name = f"ix_unanswered_questions_{column}"
        if not _has_index(_QUESTIONS, name):
            op.create_index(name, _QUESTIONS, [column])
    if not _has_index(_QUESTIONS, "ix_unanswered_questions_assistant_message_id"):
        op.create_index(
            "ix_unanswered_questions_assistant_message_id",
            _QUESTIONS,
            ["assistant_message_id"],
            unique=True,
        )

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _RLS_TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR ALL "
            f"USING ({_CTX} IS NULL OR tenant_id = {_CTX}::uuid)"
        )


def downgrade() -> None:
    # Documentation only — never executed against a shared or production
    # database (see project CLAUDE.md). Dropping the tables erases the queue
    # and every unpublished draft; dropping the columns erases the offer
    # markers and the badge stamps.
    op.drop_table(_QUESTIONS)
    op.drop_table(_GROUPS)
    op.drop_column("users", "knowledge_queue_seen_at")
    op.drop_column("messages", "escalation_offer_reason")
