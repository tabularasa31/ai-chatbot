"""Introduce tenant_api_keys table for widget API key rotation

The single ``tenants.api_key`` column made it impossible to rotate a
leaked widget key without immediately breaking the customer's embedded
widget (cf. PR #521 leak). Replace it with a 1:N table that supports a
primary "active" key plus a "revoking" grace window for the old key.

Backfill: every existing ``tenants.api_key`` becomes one ACTIVE row in
``tenant_api_keys`` (key_hash = sha256(plaintext)). The legacy column is
then dropped — widget lookup goes through the new table by hash.

Idempotent: each step inspects live state before acting.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

revision = "tenant_api_keys_v1"
down_revision = "add_documents_language_v1"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, name: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    dialect = bind.dialect.name

    # 1. Create the table if it does not already exist.
    if not _has_table(insp, "tenant_api_keys"):
        if dialect == "postgresql":
            op.execute(
                text(
                    """
                    CREATE TABLE tenant_api_keys (
                        id UUID PRIMARY KEY,
                        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                        key_hash VARCHAR(64) NOT NULL UNIQUE,
                        key_hint VARCHAR(8) NOT NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'active',
                        created_at TIMESTAMP NOT NULL,
                        expires_at TIMESTAMP NULL,
                        revoked_at TIMESTAMP NULL,
                        revoked_reason VARCHAR(32) NULL,
                        last_used_at TIMESTAMP NULL,
                        created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL
                    )
                    """
                )
            )
            op.execute(
                text(
                    "CREATE INDEX ix_tenant_api_keys_tenant_id "
                    "ON tenant_api_keys (tenant_id)"
                )
            )
            op.execute(
                text(
                    "CREATE INDEX ix_tenant_api_keys_key_hash "
                    "ON tenant_api_keys (key_hash)"
                )
            )
            op.execute(
                text(
                    "CREATE INDEX ix_tenant_api_keys_tenant_status "
                    "ON tenant_api_keys (tenant_id, status)"
                )
            )
        else:
            op.execute(
                text(
                    """
                    CREATE TABLE tenant_api_keys (
                        id CHAR(36) PRIMARY KEY,
                        tenant_id CHAR(36) NOT NULL,
                        key_hash VARCHAR(64) NOT NULL UNIQUE,
                        key_hint VARCHAR(8) NOT NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'active',
                        created_at DATETIME NOT NULL,
                        expires_at DATETIME NULL,
                        revoked_at DATETIME NULL,
                        revoked_reason VARCHAR(32) NULL,
                        last_used_at DATETIME NULL,
                        created_by_user_id CHAR(36) NULL,
                        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
                        FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                    )
                    """
                )
            )
            op.execute(
                text(
                    "CREATE INDEX ix_tenant_api_keys_tenant_id "
                    "ON tenant_api_keys (tenant_id)"
                )
            )
            op.execute(
                text(
                    "CREATE INDEX ix_tenant_api_keys_key_hash "
                    "ON tenant_api_keys (key_hash)"
                )
            )
            op.execute(
                text(
                    "CREATE INDEX ix_tenant_api_keys_tenant_status "
                    "ON tenant_api_keys (tenant_id, status)"
                )
            )

    # 2. Backfill from legacy tenants.api_key column (if still present).
    if _has_column(insp, "tenants", "api_key"):
        rows = bind.execute(
            text("SELECT id, api_key FROM tenants WHERE api_key IS NOT NULL")
        ).fetchall()
        now = datetime.now(UTC)
        insert_stmt = text(
            """
            INSERT INTO tenant_api_keys
                (id, tenant_id, key_hash, key_hint, status, created_at)
            VALUES
                (:id, :tenant_id, :key_hash, :key_hint, 'active', :created_at)
            """
        )
        for row in rows:
            tenant_id, plain = row[0], row[1]
            key_hash = hashlib.sha256(plain.encode("utf-8")).hexdigest()
            # Skip rows that were already migrated (idempotency).
            existing = bind.execute(
                text("SELECT 1 FROM tenant_api_keys WHERE key_hash = :h"),
                {"h": key_hash},
            ).first()
            if existing:
                continue
            bind.execute(
                insert_stmt,
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_id),
                    "key_hash": key_hash,
                    "key_hint": plain[-4:],
                    "created_at": now,
                },
            )

        # 3. Drop the legacy column.
        if dialect == "postgresql":
            op.execute(text("ALTER TABLE tenants DROP COLUMN api_key"))
        else:
            with op.batch_alter_table("tenants") as batch_op:
                batch_op.drop_column("api_key")


def downgrade() -> None:
    # Documented no-op: dropping the new table would lose rotated keys.
    pass
