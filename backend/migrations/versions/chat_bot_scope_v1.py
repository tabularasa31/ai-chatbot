"""Bind widget chat sessions to a bot when available.

Add nullable chats.bot_id so widget sessions can be scoped to the
public bot that created/resumed them. The column stays nullable because
dashboard/internal chat flows are still tenant-scoped.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "chat_bot_scope_v1"
down_revision = ("eval_sessions_tenant_to_bot", "id_prefixes_ck_ch_v1")
branch_labels = None
depends_on = None


def _col(insp, table: str, name: str) -> bool:
    return any(c["name"] == name for c in insp.get_columns(table))


def _idx(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def _fk(insp, table: str, name: str) -> bool:
    return any(f["name"] == name for f in insp.get_foreign_keys(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _col(insp, "chats", "bot_id"):
        with op.batch_alter_table("chats") as batch_op:
            batch_op.add_column(sa.Column("bot_id", sa.UUID(), nullable=True))

    insp = sa_inspect(bind)
    with op.batch_alter_table("chats") as batch_op:
        if not _fk(insp, "chats", "fk_chats_bot_id_bots"):
            batch_op.create_foreign_key(
                "fk_chats_bot_id_bots",
                "bots",
                ["bot_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if not _idx(insp, "chats", "ix_chats_bot_id"):
            batch_op.create_index("ix_chats_bot_id", ["bot_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    if not _col(insp, "chats", "bot_id"):
        return

    with op.batch_alter_table("chats") as batch_op:
        if _idx(insp, "chats", "ix_chats_bot_id"):
            batch_op.drop_index("ix_chats_bot_id")
        if _fk(insp, "chats", "fk_chats_bot_id_bots"):
            batch_op.drop_constraint("fk_chats_bot_id_bots", type_="foreignkey")
        batch_op.drop_column("bot_id")
