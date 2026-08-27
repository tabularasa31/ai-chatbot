"""Operator handoff observability — one row per operator-served stretch.

``chat_session_ended`` is emitted at most once per chat and measures from
``chats.created_at``. An operator can reopen a chat that was already reported
as ended, so a second emission would restate the first event with the idle
wait folded in — doubling session counts and inflating average duration. That
is why re-arming ``chats.session_ended_event_at`` was reverted in 1bd8bd5,
and it left the operator-served stretch reported nowhere at all.

``operator_sessions`` is that missing record: opened when a chat goes
``live``, stamped with the first human reply, closed by whichever path hands
the chat back, and reported as ``operator_session_ended``. A row rather than
columns on ``chats`` because a stretch is repeatable — operator releases, the
bot answers, an operator takes over again — and a marker pair on the chat
would silently overwrite the first stretch.

``escalation_ticket_id`` anchors time-to-first-human-reply to the moment the
visitor asked for a human. Measuring from ``joined_at`` would measure nothing:
taking a chat and answering in it are the same moment.

Two indexes only, on a table written roughly once per handoff:
``(chat_id, joined_at)`` for the console's per-chat history, and a **unique**
partial ``chat_id WHERE ended_at IS NULL``. The second does double duty — it
enforces "at most one open stretch per chat", without which two simultaneous
ingests would each insert a row and one human-served stretch would report two
``operator_session_ended`` events, and its predicate is exactly the sweeper's
reconciliation scan, which carries no tenant or chat filter and so cannot use
the composite.

RLS is applied here in the same revision (the table has a non-nullable
``tenant_id``); the policy shape is a frozen snapshot, deliberately not
imported from ``backend/core/rls.py``.

Revision ID: operator_sessions_v1
Revises: operator_claim_bounce_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "operator_sessions_v1"
down_revision = "operator_claim_bounce_v1"
branch_labels = None
depends_on = None

_TABLE = "operator_sessions"
_POLICY = "operator_sessions_tenant_isolation"
# NULL when the GUC is unset or empty — the fail-open branch of the policy.
_CTX = "NULLIF(current_setting('app.tenant_id', true), '')"


def _has_table(table: str) -> bool:
    if op.get_context().as_sql:
        return False
    try:
        return table in set(sa.inspect(op.get_bind()).get_table_names())
    except Exception:
        return False


def _has_index(table: str, name: str) -> bool:
    """Checked per index, not folded into :func:`_has_table`.

    A replay over a table that exists but is missing an index has to repair
    it — the unique partial index below is a correctness constraint, not a
    performance detail, so a table left without it is a table that will
    silently accept a second open stretch.
    """
    if op.get_context().as_sql:
        return False
    try:
        indexes = sa.inspect(op.get_bind()).get_indexes(table)
    except Exception:
        return False
    return name in {i["name"] for i in indexes}


def upgrade() -> None:
    if not _has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "operator_user_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column(
                "escalation_ticket_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("joined_at", sa.DateTime(), nullable=False),
            sa.Column("first_reply_at", sa.DateTime(), nullable=True),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            # Length 32 rather than sized to today's longest value: the
            # ``escalation_tickets.trigger`` VARCHAR(15) is the trap this
            # avoids, where adding an enum value needs a column alteration.
            sa.Column("ended_reason", sa.String(length=32), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
            # SET NULL on both: deleting a user, or resolving away a ticket,
            # must never delete the record that a human handled the request.
            sa.ForeignKeyConstraint(
                ["operator_user_id"], ["users.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["escalation_ticket_id"],
                ["escalation_tickets.id"],
                ondelete="SET NULL",
            ),
        )
    if not _has_index(_TABLE, "ix_operator_sessions_chat_joined"):
        op.create_index(
            "ix_operator_sessions_chat_joined", _TABLE, ["chat_id", "joined_at"]
        )
    if not _has_index(_TABLE, "uq_operator_sessions_open"):
        op.create_index(
            "uq_operator_sessions_open",
            _TABLE,
            ["chat_id"],
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
            sqlite_where=sa.text("ended_at IS NULL"),
        )

    if op.get_bind().dialect.name != "postgresql":
        return
    # Idempotent (DROP POLICY IF EXISTS before CREATE), so a repair replay over
    # an existing table still lands the second isolation contour.
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} FOR ALL "
        f"USING ({_CTX} IS NULL OR tenant_id = {_CTX}::uuid)"
    )


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project
    # CLAUDE.md). Keep this a raise, not a drop, so an accidental
    # `alembic downgrade` errors out instead of silently destroying the only
    # record of which conversations a human handled and how fast.
    raise NotImplementedError("downgrade is not supported for this migration")
