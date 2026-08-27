"""Team invitations — asked, not yet joined.

Membership lives on ``users`` (``tenant_id`` + ``role``). This table holds the
state before that: someone has been asked to join and has not yet answered.

It is a table rather than columns on ``users`` for two reasons. An invitee may
have no user row at all — the state belongs to the pair (workspace, address),
not to a person who may not exist yet. And anything written onto ``users`` at
invite time *is* the membership: a ``tenant_id`` set before acceptance grants
the chat logs, the escalations inbox and transcripts holding customers'
original wording to someone who has agreed to nothing.

``token`` is unique and nullable — cleared on acceptance, so a spent link
cannot be replayed even inside its expiry window, and NULL is exempt from
uniqueness in both dialects. ``accepted_at`` keeps the row afterwards as the
record that the invitation was answered.

One row per (tenant_id, email): re-inviting overwrites the token, expiry and
role in place, so a lost e-mail is fixed by sending another and the previous
link dies. That is a unique constraint rather than a convention, because two
live invitations for one address to one workspace would mean two links that
grant different roles.

RLS is applied here in the same revision (the table has a non-nullable
``tenant_id``); the policy shape is a frozen snapshot, deliberately not
imported from ``backend/core/rls.py``. The policy is fail-open with no tenant
context set, which is what the accept path needs: it resolves an invitation by
token before any tenant is known.

Revision ID: tenant_invitations_v1
Revises: operator_sessions_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "tenant_invitations_v1"
down_revision = "operator_sessions_v1"
branch_labels = None
depends_on = None

_TABLE = "tenant_invitations"
_POLICY = "tenant_invitations_tenant_isolation"
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
            sa.Column("email", sa.String(length=255), nullable=False),
            # Length 32 rather than sized to today's two values, so adding a
            # third role never needs a column alteration.
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("token", sa.String(length=128), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.Column(
                "invited_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            # SET NULL: an owner leaving must not delete the record of who was
            # invited, nor block their own removal.
            sa.ForeignKeyConstraint(
                ["invited_by_user_id"], ["users.id"], ondelete="SET NULL"
            ),
            sa.UniqueConstraint(
                "tenant_id", "email", name="uq_tenant_invitations_tenant_email"
            ),
        )
    if not _has_index(_TABLE, "ix_tenant_invitations_tenant_id"):
        op.create_index("ix_tenant_invitations_tenant_id", _TABLE, ["tenant_id"])
    if not _has_index(_TABLE, "ix_tenant_invitations_token"):
        # Unique: the accept path resolves a link by token alone, with no
        # tenant to narrow it.
        op.create_index(
            "ix_tenant_invitations_token", _TABLE, ["token"], unique=True
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
    """Documentation only — never executed (see the repo's Alembic rules).

    Dropping this table would destroy every outstanding invitation, which is
    unrecoverable: the tokens exist only here and in already-sent e-mail.
    """
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.drop_index("ix_tenant_invitations_token", table_name=_TABLE)
    op.drop_index("ix_tenant_invitations_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
