"""Enable Postgres row-level security on tenant-scoped tables.

Defence-in-depth for tenant isolation (see backend/core/rls.py for the full
design). Policies are fail-open when the ``app.tenant_id`` GUC is unset, so
background jobs, cron sweeps and auth-boundary lookups are unaffected; once
the backend sets the GUC for a request, Postgres refuses rows of other
tenants even if an ORM filter is missing.

The table lists below are a frozen snapshot of backend/core/rls.py at the
time this migration was written — do NOT import the app module here, or a
fresh-database replay would apply a future table list against this
revision's schema. Adding a new tenant-scoped table requires a new
migration.

Enforcement note: RLS does not apply to superusers. On deployments where
DATABASE_URL connects as a superuser this migration is inert but harmless;
enforcement starts once the app connects as a non-superuser role (FORCE ROW
LEVEL SECURITY covers non-superuser table owners).

Revision ID: rls_tenant_isolation_v1
Revises: tenant_profile_backfill_v1
"""

from __future__ import annotations

from alembic import op

revision = "rls_tenant_isolation_v1"
down_revision = "tenant_profile_backfill_v1"
branch_labels = None
depends_on = None

# NULL when the GUC is unset or empty — the fail-open branch of every policy.
_CTX = "NULLIF(current_setting('app.tenant_id', true), '')"

# Tables with a tenant_id column, isolated directly.
_TENANT_SCOPED = (
    "background_jobs",
    "chats",
    "contact_sessions",
    "documents",
    "escalation_tickets",
    "gap_analyzer_jobs",
    "gap_clusters",
    "gap_dismissals",
    "gap_doc_topics",
    "gap_questions",
    "log_analysis_state",
    "message_embeddings",
    "pii_events",
    "quick_answers",
    "tenant_faq",
    "tenant_profiles",
    "url_sources",
)

# tenant_id is nullable by design (NULL = global row): keep such rows visible.
_NULLABLE_TENANT = frozenset({"background_jobs"})

# Tables without tenant_id, isolated via a parent-FK EXISTS policy.
_CHILD_SCOPED = {
    "embeddings": ("document_id", "documents"),
    "gap_question_message_links": ("chat_id", "chats"),
    "messages": ("chat_id", "chats"),
    "url_source_runs": ("source_id", "url_sources"),
}


def _all_tables() -> list[str]:
    return list(_TENANT_SCOPED) + list(_CHILD_SCOPED)


def _using_clause(table: str) -> str:
    if table in _CHILD_SCOPED:
        fk_col, parent = _CHILD_SCOPED[table]
        return (
            f"{_CTX} IS NULL OR EXISTS ("
            f"SELECT 1 FROM {parent} p "
            f"WHERE p.id = {table}.{fk_col} AND p.tenant_id = {_CTX}::uuid)"
        )
    null_branch = " OR tenant_id IS NULL" if table in _NULLABLE_TENANT else ""
    return f"{_CTX} IS NULL{null_branch} OR tenant_id = {_CTX}::uuid"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _all_tables():
        policy = f"{table}_tenant_isolation"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR ALL "
            f"USING ({_using_clause(table)})"
        )


def downgrade() -> None:
    # Metadata-only revert (no data touched); still, per project rules,
    # downgrades are documentation — never run them against shared DBs.
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _all_tables():
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
