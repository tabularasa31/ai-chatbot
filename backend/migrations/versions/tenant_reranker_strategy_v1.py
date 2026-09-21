"""Add per-tenant retrieval reranking strategy

``tenants.reranker_strategy`` selects which reranker re-orders the fused
retrieval pool for that tenant: ``heuristic`` (the existing weighted blend,
default), ``llm`` (LLM relevance judge on the tenant's OpenAI key) or
``cross_encoder`` (local sentence-transformers cross-encoder). Semantic
strategies fall back to the heuristic on timeout or error, so the column only
ever widens behaviour — the default keeps every tenant on today's ranking.

Idempotent: inspects live state before acting.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "tenant_reranker_strategy_v1"
down_revision = "legacy_closed_chats_marker_v1"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    insp = sa_inspect(op.get_bind())
    if not _has_column(insp, "tenants", "reranker_strategy"):
        op.add_column(
            "tenants",
            sa.Column(
                "reranker_strategy",
                sa.String(length=16),
                nullable=False,
                server_default="heuristic",
            ),
        )


def downgrade() -> None:
    # Documented for completeness; never run against shared/production DBs
    # (see global CLAUDE.md). Every tenant returns to the heuristic reranker.
    insp = sa_inspect(op.get_bind())
    if _has_column(insp, "tenants", "reranker_strategy"):
        op.drop_column("tenants", "reranker_strategy")
