from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.models import TenantFaq as TenantFaqModel
from backend.tenant_knowledge.schemas import FaqCandidate
from backend.utils.math import cosine_similarity as _cosine_similarity

logger = logging.getLogger(__name__)
DEDUP_SIMILARITY_THRESHOLD = 0.92
FAQ_MIN_CONFIDENCE_THRESHOLD = 0.5


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


def insert_new_faq_candidates(
    *,
    db: Session,
    tenant_id: uuid.UUID,
    faq_candidates: Iterable[FaqCandidate],
    api_key: str,
    document_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
) -> None:
    """Insert medium/high confidence FAQ candidates; skip low and duplicates."""
    openai_client = get_openai_client(api_key)
    correlation_batch_id = batch_id or uuid.uuid4()
    total_candidates = 0
    skipped_low_confidence = 0
    skipped_empty = 0
    skipped_duplicate = 0
    inserted = 0
    auto_approved = 0
    candidate_errors = 0

    for candidate in faq_candidates:
        total_candidates += 1
        try:
            if (
                candidate.confidence is None
                or candidate.confidence < FAQ_MIN_CONFIDENCE_THRESHOLD
            ):
                skipped_low_confidence += 1
                logger.info(
                    "FAQ candidate skipped: low confidence "
                    "(batch_id=%s document_id=%s tenant_id=%s question=%r confidence=%s source=%s)",
                    correlation_batch_id,
                    document_id,
                    tenant_id,
                    candidate.question,
                    candidate.confidence,
                    candidate.source,
                )
                continue

            question = candidate.question.strip()
            answer = candidate.answer.strip()
            if not question or not answer:
                skipped_empty += 1
                logger.info(
                    "FAQ candidate skipped: empty normalized question/answer "
                    "(batch_id=%s document_id=%s tenant_id=%s question=%r source=%s)",
                    correlation_batch_id,
                    document_id,
                    tenant_id,
                    candidate.question,
                    candidate.source,
                )
                continue

            embedding_resp = openai_client.embeddings.create(
                model=settings.embedding_model,
                input=question,
            )
            question_embedding = embedding_resp.data[0].embedding  # 1536 floats
            approved = candidate.confidence >= 0.85
            inserted_candidate = False
            skipped_as_duplicate = False

            # Isolate DB-side failures per candidate so one bad insert/query
            # does not roll back earlier candidates in the same batch.
            with db.begin_nested():
                if _dedupe_existing_faq_by_similarity(
                    db=db,
                    tenant_id=tenant_id,
                    question_embedding=question_embedding,
                ):
                    skipped_duplicate += 1
                    skipped_as_duplicate = True
                else:
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
                    db.flush()
                    inserted += 1
                    if approved:
                        auto_approved += 1
                    inserted_candidate = True

            if inserted_candidate:
                logger.info(
                    "FAQ candidate queued for insert "
                    "(batch_id=%s document_id=%s tenant_id=%s question=%r confidence=%.3f source=%s approved=%s)",
                    correlation_batch_id,
                    document_id,
                    tenant_id,
                    question,
                    float(candidate.confidence),
                    candidate.source,
                    approved,
                )
            elif skipped_as_duplicate:
                logger.info(
                    "FAQ candidate skipped: semantic duplicate "
                    "(batch_id=%s document_id=%s tenant_id=%s question=%r confidence=%.3f source=%s)",
                    correlation_batch_id,
                    document_id,
                    tenant_id,
                    question,
                    float(candidate.confidence),
                    candidate.source,
                )
        except Exception:
            # Best-effort: don't let one bad candidate break the whole batch.
            candidate_errors += 1
            logger.exception(
                "Failed to insert FAQ candidate "
                "(batch_id=%s document_id=%s tenant_id=%s)",
                correlation_batch_id,
                document_id,
                tenant_id,
            )
            continue

    db.commit()
    logger.info(
        "FAQ insert summary "
        "(batch_id=%s document_id=%s tenant_id=%s total=%s inserted=%s auto_approved=%s skipped_low_confidence=%s "
        "skipped_empty=%s skipped_duplicate=%s candidate_errors=%s)",
        correlation_batch_id,
        document_id,
        tenant_id,
        total_candidates,
        inserted,
        auto_approved,
        skipped_low_confidence,
        skipped_empty,
        skipped_duplicate,
        candidate_errors,
    )
