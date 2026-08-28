"""Move the operator entitlement from the workspace to the person

Four steps, one revision, because they are one change:

* ``users.seat_granted_at`` — nullable. NULL means no seat; a timestamp means
  the moment one was granted. Nullable rather than a boolean plus a separate
  date: one column answers both "does this person hold a seat" and "since
  when", and there is no third state to encode.
* ``tenants.plan`` — dropped. It was added one revision earlier
  (``tenant_plan_v1``) to model the same entitlement at the workspace level.
  With the entitlement on the person it says nothing that
  ``EXISTS (SELECT 1 FROM users WHERE tenant_id = ... AND seat_granted_at IS
  NOT NULL)`` does not say better, and two spellings of one fact drift.
* **One owner per workspace.** Any workspace holding more than one owner keeps
  the earliest verified one and the rest become operators.
* **Every member who has already joined gets a seat.** Owners do not.

The last two are the same act as the first two: making the data say what the
model says.

**Why the role normalisation.** The build this migration ships with has no way
to change a role — no promotion, no demotion, no role on an invitation — so a
workspace that acquired a second owner through the routes this release deletes
would be frozen that way: two owners, neither able to remove the other,
and no surface left that could repair it. A pending invitation created as
``role='owner'`` before the deploy is the same problem arriving later, since
accepting it after the deploy would mint the second owner. Ranking by
``is_verified DESC, created_at ASC`` keeps the workspace's founder: they are
its oldest member, and everybody promoted into ownership was invited after
them. It never leaves a workspace with zero owners, because it keeps a row
rather than counting one.

This is the one destructive step here, and it is deliberate: an unmanageable
two-owner workspace is worse than a demotion that can be explained. There are
no live tenants.

**Why the seat backfill.** Under this model a member who has joined holds a
seat — that is what the invitation grants, and what the removal releases.
Somebody who accepted before this migration ran would otherwise be verified
with ``seat_granted_at IS NULL`` for good: the grant only fires for a *pending*
invitee accepting, so no password reset can seat them, and the seat routes act
on the caller and admit only an owner, so neither they nor their owner can fix
it. Every ``/operator/*`` call would 403 forever and the only remedy would be
deleting the account and re-inviting, which rewrites their message and API-key
history to a label. Backfilling makes the data agree with the model rather
than inventing anything, and it grants nobody a cost: nothing is charged.

Owners keep NULL. A workspace's owner administers without a seat and takes one
only if they also mean to answer from the console, so seating them here would
be the invention that seating a member is not.

Idempotent: it inspects live state before altering the schema, and both data
steps are written so a second run changes nothing.

Revision ID: operator_seats_v1
Revises: member_removal_signatures_v1
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "operator_seats_v1"
down_revision = "member_removal_signatures_v1"
branch_labels = None
depends_on = None

ROLE_OWNER = "owner"
ROLE_OPERATOR = "operator"

#: Enough of ``users`` to write the two data steps in Core rather than raw
#: SQL, so the booleans and timestamps are rendered by the dialect.
_users = sa.table(
    "users",
    sa.column("id", sa.Uuid),
    sa.column("tenant_id", sa.Uuid),
    sa.column("role", sa.String),
    sa.column("is_verified", sa.Boolean),
    sa.column("seat_granted_at", sa.DateTime),
    sa.column("created_at", sa.DateTime),
)


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def _one_owner_per_workspace(bind) -> None:
    """Demote every owner but the founder, workspace by workspace.

    Row by row rather than one window-function UPDATE: the population is a
    handful of workspaces at most, and this runs on whatever database the
    developer pointed alembic at rather than only on PostgreSQL.
    """
    crowded = bind.execute(
        sa.select(_users.c.tenant_id)
        .where(_users.c.tenant_id.isnot(None), _users.c.role == ROLE_OWNER)
        .group_by(_users.c.tenant_id)
        .having(sa.func.count() > 1)
    ).scalars().all()

    for tenant_id in crowded:
        keeper = bind.execute(
            sa.select(_users.c.id)
            .where(_users.c.tenant_id == tenant_id, _users.c.role == ROLE_OWNER)
            .order_by(
                _users.c.is_verified.desc(),
                _users.c.created_at.asc(),
                _users.c.id.asc(),
            )
            .limit(1)
        ).scalar_one()
        bind.execute(
            sa.update(_users)
            .where(
                _users.c.tenant_id == tenant_id,
                _users.c.role == ROLE_OWNER,
                _users.c.id != keeper,
            )
            .values(role=ROLE_OPERATOR)
        )


def _seat_everyone_who_has_joined(bind) -> None:
    """Give a seat to every verified member who is not an owner.

    ``seat_granted_at IS NULL`` in the predicate is what makes this safe to
    run twice, and it cannot re-seat somebody who gave a seat up: only an
    owner can do that, and owners are excluded here.
    """
    bind.execute(
        sa.update(_users)
        .where(
            _users.c.tenant_id.isnot(None),
            _users.c.is_verified.is_(True),
            _users.c.role != ROLE_OWNER,
            _users.c.seat_granted_at.is_(None),
        )
        .values(seat_granted_at=dt.datetime.now(dt.UTC).replace(tzinfo=None))
    )


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

    # Roles first: a member demoted here is then seated by the step below,
    # which is right — they are an operator now, and operators hold seats.
    _one_owner_per_workspace(bind)
    _seat_everyone_who_has_joined(bind)


def downgrade() -> None:
    # Documented for completeness; never run against shared or production
    # databases (see the global Alembic rules). Reversing this loses which
    # people held seats and cannot restore a demoted owner — nothing records
    # that they were one. ``tenants.plan`` comes back on its original ``free``
    # default rather than on whatever it held before it was dropped; the
    # column's contents are not recoverable from the seats that replaced it.
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
