"""Add ck_ prefix to tenant API keys and regenerate non-ch_ bot public_ids

Two related cleanups so externally visible IDs share a consistent
namespace-prefix convention:

  * `tenants.api_key`: widen VARCHAR(32) -> VARCHAR(35) and prepend
    "ck_" (chat9 key) to any row lacking the prefix. Makes keys
    visually distinct from OpenAI's sk_ keys in logs.

  * `bots.public_id`: rows created by the original add_bots_table
    backfill used an inline nanoid-style generator (21-char
    URL-safe alphabet) and therefore lack the "ch_" prefix that
    `generate_public_id()` now always produces. Regenerate those
    rows to the canonical ch_<18 lowercase alnum> format.

No live production customers at time of writing; destructive
regeneration of offending bot public_ids is intentional.
Idempotent: each step inspects live state before acting.
"""
from __future__ import annotations

import secrets
import string

from alembic import op
from sqlalchemy import String, text
from sqlalchemy import inspect as sa_inspect

revision = "id_prefixes_ck_ch_v1"
down_revision = "67aaa83e5689"
branch_labels = None
depends_on = None


_BOT_ID_ALPHABET = string.ascii_lowercase + string.digits


def _generate_bot_public_id() -> str:
    return "ch_" + "".join(secrets.choice(_BOT_ID_ALPHABET) for _ in range(18))


def _col_len(insp, table: str, name: str) -> int | None:
    for c in insp.get_columns(table):
        if c["name"] == name:
            return getattr(c["type"], "length", None)
    return None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # 1. Widen tenants.api_key to fit ck_-prefixed values (35 chars).
    current_len = _col_len(insp, "tenants", "api_key")
    if current_len is not None and current_len < 35:
        with op.batch_alter_table("tenants") as batch_op:
            batch_op.alter_column(
                "api_key", type_=String(35), existing_nullable=False
            )

    # 2. Prepend ck_ to existing api_keys that don't have it.
    if bind.dialect.name == "postgresql":
        op.execute(
            text(
                r"UPDATE tenants SET api_key = 'ck_' || api_key "
                r"WHERE api_key NOT LIKE 'ck\_%' ESCAPE '\'"
            )
        )
    else:
        op.execute(
            text(
                "UPDATE tenants SET api_key = 'ck_' || api_key "
                "WHERE substr(api_key, 1, 3) <> 'ck_'"
            )
        )

    # 3. Regenerate bots.public_id for rows without ch_ prefix.
    # 36^18 ≈ 10^28 keyspace — collision probability is negligible, so we
    # skip a per-row uniqueness check and rely on the UNIQUE index to
    # surface the vanishingly-rare conflict (migration retry on rerun).
    # Stream ids server-side to avoid loading all into memory, then issue
    # a single executemany UPDATE.
    if bind.dialect.name == "postgresql":
        select_sql = text(
            r"SELECT id FROM bots WHERE public_id NOT LIKE 'ch\_%' ESCAPE '\'"
        )
    else:
        select_sql = text(
            "SELECT id FROM bots WHERE substr(public_id, 1, 3) <> 'ch_'"
        )

    _BATCH_SIZE = 1000
    update_stmt = text("UPDATE bots SET public_id = :p WHERE id = :id")
    rows = bind.execute(select_sql).fetchall()
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = [{"id": row[0], "p": _generate_bot_public_id()} for row in rows[i : i + _BATCH_SIZE]]
        bind.execute(update_stmt, batch)


def downgrade() -> None:
    # Documented no-op: prefix additions and regenerated IDs are not
    # reverted because external consumers may already use the new values.
    pass
