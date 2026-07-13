"""Enable Postgres row-level security on guard_events.

Second isolation contour for the guard-verdict log (see backend/core/rls.py).
guard_events has a non-nullable tenant_id, so it takes the standard
tenant-scoped policy: fail-open when the ``app.tenant_id`` GUC is unset (the
fire-and-forget recorder inherits the request's tenant context, and background
paths without context keep working), else ``tenant_id = app.tenant_id``.

The table name and policy shape below are a frozen snapshot — do NOT import
the app module here, so a fresh-database replay applies exactly this
revision's intent.

Revision ID: guard_events_rls_v1
Revises: guard_events_v1
"""

from __future__ import annotations

from alembic import op

revision = "guard_events_rls_v1"
down_revision = "guard_events_v1"
branch_labels = None
depends_on = None

# NULL when the GUC is unset or empty — the fail-open branch of the policy.
_CTX = "NULLIF(current_setting('app.tenant_id', true), '')"
_TABLE = "guard_events"
_POLICY = "guard_events_tenant_isolation"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} FOR ALL "
        f"USING ({_CTX} IS NULL OR tenant_id = {_CTX}::uuid)"
    )


def downgrade() -> None:
    # Metadata-only revert; per project rules downgrades are documentation and
    # are never run against shared DBs.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
