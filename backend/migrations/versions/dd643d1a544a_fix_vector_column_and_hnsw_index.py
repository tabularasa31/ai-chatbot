"""fix_vector_column_and_hnsw_index

Revision ID: dd643d1a544a
Revises: add_reset_password
Create Date: 2026-03-20 11:23:05.756740

"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = 'dd643d1a544a'
down_revision = 'add_reset_password'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Ensure pgvector extension is enabled
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Drop the old incorrect B-tree index
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector")

    # 3. Drop the old wrong-type column and recreate as vector(1536)
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS vector")
    op.execute("ALTER TABLE embeddings ADD COLUMN vector vector(1536)")

    # 4. Backfill: migrate vectors from metadata JSON → vector column
    op.execute("""
        UPDATE embeddings
        SET vector = (metadata->>'vector')::vector
        WHERE metadata->>'vector' IS NOT NULL
          AND vector IS NULL
    """)

    # 5. Create HNSW index for cosine distance
    op.execute("""
        CREATE INDEX ix_embeddings_vector_hnsw
        ON embeddings
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
    # Keep this as raise, not pass, so accidental `alembic downgrade` errors out
    # instead of silently moving `alembic_version` backward while schema stays.
    raise NotImplementedError("downgrade is not supported for this migration")
