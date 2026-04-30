"""Pytest fixtures for the multi-hop retrieval baseline eval.

Reuses the pgvector engine + database lifecycle from
``tests/pgvector_tests/conftest.py`` (real PostgreSQL with the vector
extension enabled), then layers in:

- A *deterministic* synthetic embedder that replaces OpenAI's embedding
  endpoint. The default pgvector_tests fixture returns a constant
  ``[0.1] * 1536`` vector for every input — that makes dense retrieval
  meaningless because every chunk is identical from the dense channel's
  POV. For an eval that measures the dense + BM25 + RRF interplay we
  need distinct, lexically-correlated vectors per chunk and per query.
- An ``indexed_corpus`` fixture that ingests the static dataset
  (``tests/eval/multi_hop/dataset.py``) into a fresh tenant before each
  test, populating ``Embedding.vector`` with synthetic embeddings.

The harness is gated by the ``pgvector`` mark — ``pytest -m pgvector
tests/eval/multi_hop/`` runs only this subtree against a live PG.
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Generator
from unittest.mock import Mock, patch

import psycopg2
import pytest
import sqlalchemy as sa
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# Mirror tests/pgvector_tests/conftest.py: ensure backend.* imports work and
# required env vars are set before any backend imports.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:?check_same_thread=False")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("ENCRYPTION_KEY", "7b4_zUZivxPZWzIkXbVf3dpQX9Ab22HB51H9Qcrjya8=")

from backend.models import (  # noqa: E402
    Base,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    Tenant,
)
from tests.eval.multi_hop import dataset as ds  # noqa: E402
from tests.eval.multi_hop.synthetic_embeddings import embed_text  # noqa: E402


def _pg_params() -> dict:
    return {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "user": os.getenv("PG_USER", "postgres"),
        "password": os.getenv("PG_PASSWORD", "password"),
    }


@pytest.fixture(scope="function")
def pg_engine() -> Generator[sa.engine.Engine, None, None]:
    """Disposable PostgreSQL database with pgvector enabled (per test)."""
    params = _pg_params()
    test_db = os.getenv("PG_DBNAME", "test_multi_hop_eval")
    password_part = f":{params['password']}@" if params["password"] else "@"
    url = (
        f"postgresql+psycopg2://{params['user']}{password_part}"
        f"{params['host']}:{params['port']}/{test_db}"
    )

    admin_conn = psycopg2.connect(**params, dbname="postgres")
    admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin_conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{test_db}"')
        cur.execute(f'CREATE DATABASE "{test_db}"')
    admin_conn.close()

    engine_ = create_engine(url, echo=False, poolclass=NullPool)
    with engine_.connect() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine_)

    os.environ["DATABASE_URL"] = url
    try:
        yield engine_
    finally:
        engine_.dispose()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:?check_same_thread=False"
        try:
            cleanup = psycopg2.connect(**params, dbname="postgres")
            cleanup.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with cleanup.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{test_db}' AND pid <> pg_backend_pid()"
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{test_db}"')
            cleanup.close()
        except Exception:
            pass


@pytest.fixture(scope="function")
def pg_db_session(pg_engine: sa.engine.Engine) -> Generator[Session, None, None]:
    PgSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=pg_engine,
        class_=Session,
        future=True,
    )
    session = PgSessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def synthetic_openai_client() -> Generator[Mock, None, None]:
    """Replace OpenAI clients with a synthetic-embedding stub.

    ``embeddings.create(input=[...])`` returns deterministic embeddings from
    ``synthetic_embeddings.embed_text`` so dense retrieval has signal that
    correlates with lexical overlap. Chat completion calls are not exercised
    by this harness, so the stub returns a no-op response.
    """
    mock_client = Mock()

    def fake_embeddings_create(*, model, input, **_kwargs):  # noqa: ARG001
        # Accept either a single string or a list (the OpenAI SDK supports both).
        if isinstance(input, str):
            inputs = [input]
        else:
            inputs = list(input)
        return Mock(data=[Mock(embedding=embed_text(text)) for text in inputs])

    mock_client.embeddings.create.side_effect = fake_embeddings_create
    mock_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content="ok"))],
        usage=Mock(total_tokens=1),
    )

    with (
        patch("backend.embeddings.service.get_openai_client", return_value=mock_client),
        patch("backend.search.service.get_openai_client", return_value=mock_client),
    ):
        yield mock_client


@pytest.fixture(scope="function")
def indexed_corpus(pg_db_session: Session) -> dict:
    """Materialize the static dataset into a fresh tenant.

    Returns a dict with:
      ``tenant_id`` — UUID of the test tenant
      ``chunk_id_to_uuid`` — map from dataset chunk_id ("pricing-pro-cost")
        to the actual ``Embedding.id`` UUID, so the harness can resolve
        retrieval results back to dataset ids without smuggling extra fields.
    """
    tenant = Tenant(name="MultiHopEvalTenant", public_id="multi-hop-eval")
    pg_db_session.add(tenant)
    pg_db_session.flush()

    # One Document per document_id, all pointing at the test tenant.
    doc_uuid: dict[str, uuid.UUID] = {}
    for doc_id in {c.document_id for c in ds.CHUNKS}:
        doc = Document(
            tenant_id=tenant.id,
            filename=f"{doc_id}.md",
            file_type=DocumentType.markdown,
            status=DocumentStatus.ready,
            parsed_text="synthetic eval corpus",
        )
        pg_db_session.add(doc)
        pg_db_session.flush()
        doc_uuid[doc_id] = doc.id

    # One Embedding per Chunk. Synthetic embeddings come from embed_text.
    # ``entities`` ships from the dataset's ground-truth so the
    # entity-overlap eval (Step 5) can exercise the third channel
    # without depending on a real NER model — the harness measures
    # *retriever* quality given good NER, not NER quality itself.
    chunk_id_to_uuid: dict[str, uuid.UUID] = {}
    for chunk in ds.CHUNKS:
        emb = Embedding(
            document_id=doc_uuid[chunk.document_id],
            chunk_text=chunk.text,
            vector=embed_text(chunk.text),
            metadata_json={"chunk_id": chunk.chunk_id},
            entities=list(chunk.entities),
        )
        pg_db_session.add(emb)
        pg_db_session.flush()
        chunk_id_to_uuid[chunk.chunk_id] = emb.id

    pg_db_session.commit()
    return {
        "tenant_id": tenant.id,
        "chunk_id_to_uuid": chunk_id_to_uuid,
        "uuid_to_chunk_id": {v: k for k, v in chunk_id_to_uuid.items()},
    }


@pytest.fixture()
def query_entities_lookup() -> dict[str, list[str]]:
    """Ground-truth query → entities mapping for stubbing NER on queries."""
    return {q.text: list(q.query_entities) for q in ds.QUERIES}
