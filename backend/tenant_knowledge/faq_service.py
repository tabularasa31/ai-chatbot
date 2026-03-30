from __future__ import annotations

import json
import math
import uuid
from typing import Iterable

from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.models import TenantFaq as TenantFaqModel
from backend.tenant_knowledge.schemas import FaqCandidate

EMBEDDING_MODEL = "text-embedding-3-small"
DEDUP_SIMILARITY_THRESHOLD = 0.92


def _vector_from_unknown(raw: object) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, list) and all(isinstance(x, (int, float)) for x in raw):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(
                isinstance(x, (int, float)) for x in parsed
            ):
                return [float(x) for x in parsed]
        except Exception:
            pass
    return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm1 = math.sqrt(sum(x * x for x in a))
    norm2 = math.sqrt(sum(y * y for y in b))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))


def _dedupe_existing_faq_by_similarity(
    *,
    db: Session,
    tenant_id: uuid.UUID,
    question_embedding: list[float],
) -> bool:
    """Return True if candidate is duplicate and should be skipped."""
    try:
        distance_expr = TenantFaqModel.question_embedding.cosine_distance(
            question_embedding
        )
        row = (
            db.query(TenantFaqModel, distance_expr.label("distance"))
            .filter(TenantFaqModel.tenant_id == tenant_id)
            .filter(TenantFaqModel.question_embedding.isnot(None))
            .order_by(distance_expr)
            .limit(1)
            .first()
        )
        if not row:
            return False
        distance = row[1]
        similarity = max(0.0, 1.0 - float(distance))
        return similarity >= DEDUP_SIMILARITY_THRESHOLD
    except Exception:
        # SQLite fallback (vector stored as TEXT for tests).
        existing = (
            db.query(TenantFaqModel)
            .filter(TenantFaqModel.tenant_id == tenant_id)
            .filter(TenantFaqModel.question_embedding.isnot(None))
            .all()
        )
        best = 0.0
        for item in existing:
            v = _vector_from_unknown(item.question_embedding)
            if v is None:
                continue
            best = max(best, _cosine_similarity(question_embedding, v))
        return best >= DEDUP_SIMILARITY_THRESHOLD


def upsert_faq_candidates(
    *,
    db: Session,
    tenant_id: uuid.UUID,
    faq_candidates: Iterable[FaqCandidate],
    api_key: str,
) -> None:
    """Insert medium/high confidence FAQ candidates; skip low and duplicates."""
    openai_client = get_openai_client(api_key)

    for candidate in faq_candidates:
        if candidate.confidence is None or candidate.confidence < 0.5:
            continue

        question = candidate.question.strip()
        answer = candidate.answer.strip()
        if not question or not answer:
            continue

        embedding_resp = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=question,
        )
        question_embedding = embedding_resp.data[0].embedding  # 1536 floats

        if not _dedupe_existing_faq_by_similarity(
            db=db,
            tenant_id=tenant_id,
            question_embedding=question_embedding,
        ):
            approved = candidate.confidence >= 0.85
            db.add(
                TenantFaqModel(
                    tenant_id=tenant_id,
                    question=question,
                    answer=answer,
                    question_embedding=question_embedding,
                    confidence=float(candidate.confidence),
                    source=candidate.source,
                    approved=approved,
                )
            )

    db.commit()

