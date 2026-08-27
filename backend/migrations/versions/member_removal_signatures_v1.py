"""Removing a member deletes the account — keep the signature on the history.

Membership and account now have the same lifetime: removing someone deletes
their user row rather than detaching it. That makes the invite path honest
(every invitee is a new account setting a first password from the link, so
nobody joins without an act of their own), and it costs two things this
revision pays for.

**The delete has to be possible at all.** Five of the six FKs into ``users``
are already ``ON DELETE SET NULL``. ``gap_dismissals.dismissed_by`` has no
``ondelete`` and is ``NOT NULL``, so the database would simply refuse. It
becomes nullable with ``SET NULL`` — not ``CASCADE``, which would delete the
dismissal and resurrect a gap the team had already decided about, turning a
personnel change into a change in the work queue.

**The delete must not erase who did the work.** ``SET NULL`` is silent: every
message the departing person wrote as an operator, and every stretch they
held, would lose its author with no trace that there had been one. Nothing
reads those fields today (the console is phase 2), so the loss would go
unnoticed until precisely the moment it mattered — "who handled this ticket"
gets asked more after somebody leaves, not less. Three ``*_label`` columns
hold the departing member's e-mail, written in the same transaction as the
delete. They stay NULL while the account exists: the author is read through
the FK, and the label is the fallback once it is gone.

Not covered, deliberately: ``chats.assigned_operator_id`` is live state (who
holds this conversation now), not history, and ``pii_events.actor_user_id`` is
never written by anything — the three surviving directions are all machine
egress, so there is no actor to preserve.

Additive and backfill-free: existing rows keep NULL labels, which is correct,
because every account they point at still exists.

Revision ID: member_removal_signatures_v1
Revises: operator_sessions_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "member_removal_signatures_v1"
down_revision = "operator_sessions_v1"
branch_labels = None
depends_on = None

_LABEL_COLUMNS = (
    ("messages", "operator_label"),
    ("operator_sessions", "operator_label"),
    ("gap_dismissals", "dismissed_by_label"),
)


def _has_column(table: str, column: str) -> bool:
    if op.get_context().as_sql:
        return False
    try:
        cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    except Exception:
        return False
    return column in cols


def _dismissed_by_fk_name() -> str | None:
    """The auto-generated name of the ``dismissed_by`` FK, discovered not guessed.

    ``gap_analyzer_phase1_v1`` created the constraint unnamed, so its name is
    whatever the server chose (``gap_dismissals_dismissed_by_fkey`` on
    PostgreSQL). Looking it up keeps the replay honest on a database whose
    constraint was named differently.
    """
    if op.get_context().as_sql:
        return "gap_dismissals_dismissed_by_fkey"
    try:
        fks = sa.inspect(op.get_bind()).get_foreign_keys("gap_dismissals")
    except Exception:
        return None
    for fk in fks:
        if fk.get("constrained_columns") == ["dismissed_by"]:
            return fk.get("name") or "gap_dismissals_dismissed_by_fkey"
    return None


def upgrade() -> None:
    for table, column in _LABEL_COLUMNS:
        if not _has_column(table, column):
            op.add_column(table, sa.Column(column, sa.String(length=255), nullable=True))

    if op.get_bind().dialect.name != "postgresql":
        # SQLite cannot ALTER a constraint; the test suite builds its schema
        # from the models with ``create_all``, where both properties are
        # already declared.
        return

    op.alter_column(
        "gap_dismissals",
        "dismissed_by",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    fk_name = _dismissed_by_fk_name()
    if fk_name:
        op.drop_constraint(fk_name, "gap_dismissals", type_="foreignkey")
    op.create_foreign_key(
        "fk_gap_dismissals_dismissed_by_users",
        "gap_dismissals",
        "users",
        ["dismissed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Documentation only — never executed (see the repo's Alembic rules).

    Reversing this is lossy in a way no migration can undo: dropping the label
    columns discards the only remaining record of who wrote the operator
    messages of anyone who has since left, and restoring ``NOT NULL`` on
    ``dismissed_by`` would fail outright against rows the deletes have already
    nulled.
    """
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "fk_gap_dismissals_dismissed_by_users", "gap_dismissals", type_="foreignkey"
        )
        op.create_foreign_key(
            None, "gap_dismissals", "users", ["dismissed_by"], ["id"]
        )
        op.alter_column(
            "gap_dismissals",
            "dismissed_by",
            existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        )
    for table, column in _LABEL_COLUMNS:
        op.drop_column(table, column)
