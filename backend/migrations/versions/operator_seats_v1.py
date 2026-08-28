"""Move the operator entitlement from the workspace to the person

Two halves of one change, so one revision:

* ``users.seat_granted_at`` — nullable. NULL means no seat; a timestamp
  means the moment one was granted. Nullable rather than a boolean plus a
  separate date: one column answers both "does this person hold a seat" and
  "since when", and there is no third state to encode.
* ``tenants.plan`` — dropped. It was added one revision earlier
  (``tenant_plan_v1``) to model the same entitlement at the workspace level.
  With the entitlement on the person it says nothing that
  ``EXISTS (SELECT 1 FROM users WHERE tenant_id = ... AND seat_granted_at IS
  NOT NULL)`` does not say better, and two spellings of one fact drift.

**Nobody is granted a seat here.** Every existing row lands on NULL, so every
existing workspace drops to the ordinary e-mail path until somebody is
invited or an owner takes a seat. That is deliberate: a backfill would be
this migration inventing purchases nobody made. There are no live tenants, so
it costs nothing; the point is that it would be wrong even if there were.

No backfill from ``tenants.plan`` either, for the same reason and one more:
the plan was a workspace-level flag with no notion of *which people*, so
there is nothing in it to spread over the members.

Idempotent: inspects live state before acting, so a re-run over a database
that already has the column (or has already lost ``plan``) is a no-op.

Revision ID: operator_seats_v1
Revises: member_removal_signatures_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "operator_seats_v1"
down_revision = "member_removal_signatures_v1"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _has_column(insp, "users", "seat_granted_at"):
        op.add_column(
            "users",
            sa.Column("seat_granted_at", sa.DateTime(), nullable=True),
        )

    if _has_column(insp, "tenants", "plan"):
        op.drop_column("tenants", "plan")


def downgrade() -> None:
    # Documented for completeness; never run against shared or production
    # databases (see the global Alembic rules). Reversing this loses which
    # people held seats, and restores ``tenants.plan`` on its original
    # ``free`` default rather than on whatever it held before it was dropped
    # — the column's contents are not recoverable from the seats that
    # replaced it.
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _has_column(insp, "tenants", "plan"):
        op.add_column(
            "tenants",
            sa.Column(
                "plan",
                sa.String(length=16),
                nullable=False,
                server_default="free",
            ),
        )

    if _has_column(insp, "users", "seat_granted_at"):
        op.drop_column("users", "seat_granted_at")
