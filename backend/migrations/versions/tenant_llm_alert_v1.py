"""Add LLM-failure alert state to tenants

When the OpenAI provider fails with an actionable failure type
(quota_exhausted or invalid_api_key) the chat pipeline now records the
failure on the tenant row so the dashboard can surface a banner and the
backend can throttle "your key is broken" emails to once per 24h.

Three nullable columns on ``tenants``:
  - ``llm_alert_type``       — current failure type (NULL = no alert)
  - ``llm_alert_first_at``   — when the current alert was first raised
  - ``llm_alert_last_email_at`` — last time we emailed the admin (for throttle)

All cleared on the next successful chat turn.

Idempotent: each step inspects live state before acting.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "tenant_llm_alert_v1"
down_revision = "drop_kyc_columns_v1"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _has_column(insp, "tenants", "llm_alert_type"):
        op.add_column(
            "tenants",
            sa.Column("llm_alert_type", sa.String(length=64), nullable=True),
        )
    if not _has_column(insp, "tenants", "llm_alert_first_at"):
        op.add_column(
            "tenants",
            sa.Column("llm_alert_first_at", sa.DateTime(), nullable=True),
        )
    if not _has_column(insp, "tenants", "llm_alert_last_email_at"):
        op.add_column(
            "tenants",
            sa.Column("llm_alert_last_email_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    # Documented for completeness; never run against shared/production DBs
    # (see global CLAUDE.md). Drops the alert state, losing in-flight signal.
    bind = op.get_bind()
    insp = sa_inspect(bind)
    for col in ("llm_alert_last_email_at", "llm_alert_first_at", "llm_alert_type"):
        if _has_column(insp, "tenants", col):
            op.drop_column("tenants", col)
