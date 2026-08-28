"""Add the subscription tier to tenants

A single ``plan`` column on ``tenants``, holding a ``TenantPlan`` value
(``free`` / ``pro``), NOT NULL with a server default of ``free`` so every
existing row lands on today's behaviour without a backfill pass.

A plan value rather than a boolean such as ``live_handoff_enabled``: it is a
customer entitlement rather than an on/off switch for our own code, and a
second paid level costs nothing at the schema level. Stored as a plain
``VARCHAR(16)`` and not a Postgres enum type, matching ``llm_alert_type`` and
``tenant_api_keys.status`` — adding a tier is then a code change rather than
an ``ALTER TYPE`` against a live database.

Nothing reads the column yet. The first consumer is the live-operator e-mail
lane, where the tier decides whether the escalation notification carries our
inbound token address in ``Reply-To`` or the visitor's own address as it does
today. No billing system exists and none is implied by this column.

``tenants`` is an auth-boundary table and is exempt from RLS (it is queried
before the tenant is known), so no policy work belongs here.

Idempotent: inspects live state before acting.

Revision ID: tenant_plan_v1
Revises: operator_sessions_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "tenant_plan_v1"
down_revision = "operator_sessions_v1"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
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


def downgrade() -> None:
    # Documented for completeness; never run against shared or production
    # databases (see the global Alembic rules). Dropping the column erases
    # which tenants had been switched to the paid tier.
    bind = op.get_bind()
    insp = sa_inspect(bind)
    if _has_column(insp, "tenants", "plan"):
        op.drop_column("tenants", "plan")
