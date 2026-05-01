from __future__ import annotations

import ast
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models import TenantFaq
from backend.search.service import cosine_similarity


@dataclass(frozen=True)
class FAQRow:
    id: uuid.UUID
    question: str
    answer: str
    approved: bool
    score: float


@dataclass(frozen=True)
class FAQMatchResult:
    strategy: str  # 'faq_direct' | 'faq_context' | 'rag_only'
    faq_items: list[FAQRow]

    top_score: float | None
    selected_score: float | None
    selected_faq_id: str | None

    direct_guard_used: bool
    direct_guard_passed: bool

    decision_reason: str


def _faq_thresholds() -> tuple[float, float, int]:
    direct_threshold = settings.faq_direct_threshold
    context_threshold = min(settings.faq_context_threshold, direct_threshold)
    context_max_items = max(settings.faq_context_max_items, 1)
    return direct_threshold, context_threshold, context_max_items


def _approved_promotion_delta() -> float:
    """
    Optional safety knob for approved-biased direct candidate selection.
    If an approved candidate is close enough to the absolute top score,
    we allow it to be considered for direct path.
    """
    return max(settings.faq_approved_promotion_delta, 0.0)


def _parse_sqlite_vector_text(raw: Any) -> list[float] | None:
    """
    SQLite tests store pgvector as TEXT.
    Try to parse a list-like string into floats.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, tuple):
        values = list(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            values = ast.literal_eval(text)
        except Exception:
            return None
    else:
        return None

    if not isinstance(values, list):
        return None
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            return None
    return out


def _fetch_top_faq_rows(
    *,
    tenant_id: uuid.UUID,
    question_embedding: list[float],
    db: Session,
    limit: int = 3,
) -> list[FAQRow]:
    """
    Fetch top FAQ candidates by semantic similarity (cosine).

    Production: use cosine_distance via pgvector operator.
    SQLite tests: fall back to Python cosine over parsed TEXT vectors.
    """
    db_url = str(getattr(db.get_bind(), "url", ""))
    if "sqlite" in db_url:
        rows = (
            db.query(TenantFaq)
            .filter(TenantFaq.tenant_id == tenant_id)
            .limit(max(limit * 5, limit))
            .all()
        )
        scored: list[FAQRow] = []
        for r in rows:
            vec = _parse_sqlite_vector_text(r.question_embedding)
            if vec is None:
                continue
            score = cosine_similarity(question_embedding, vec)
            scored.append(
                FAQRow(
                    id=r.id,
                    question=r.question,
                    answer=r.answer,
                    approved=bool(r.approved),
                    score=float(score),
                )
            )
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

    # Postgres path: delegate similarity to pgvector via SQLAlchemy.
    # cosine_distance returns a distance in [0..2] sometimes; we convert to similarity.
    distance_expr = TenantFaq.question_embedding.cosine_distance(question_embedding)
    results = (
        db.query(TenantFaq, distance_expr.label("distance"))
        .filter(TenantFaq.tenant_id == tenant_id)
        .order_by(distance_expr)
        .limit(limit)
        .all()
    )
    out: list[FAQRow] = []
    for faq, distance in results:
        try:
            sim = max(0.0, 1.0 - float(distance))
        except (TypeError, ValueError):
            sim = 0.0
        out.append(
            FAQRow(
                id=faq.id,
                question=faq.question,
                answer=faq.answer,
                approved=bool(faq.approved),
                score=float(sim),
            )
        )
    return out


def direct_applicability_guard(
    *,
    question: str,
    faq_question: str,
    faq_answer: str,
) -> bool:
    """
    Cheap precision guard for `faq_direct`.

    Contract:
    - Must not call retrieval (no DB queries, no pgvector).
    - Must return a binary decision for whether we can safely answer directly.
    - Any uncertainty should be treated as a failure (return False).
    """
    # Rule-based lexical/structural equivalence check.
    # This is intentionally conservative to avoid false positives.
    q = question.strip().casefold()
    f = faq_question.strip().casefold()
    if not q or not f:
        return False

    def _tokens(s: str) -> set[str]:
        # Keep alnum words only.
        return {t for t in "".join(ch if ch.isalnum() else " " for ch in s).split() if t}

    q_tokens = _tokens(q)
    f_tokens = _tokens(f)
    if not q_tokens or not f_tokens:
        return False

    overlap = len(q_tokens & f_tokens)
    denom = max(len(q_tokens), 1)
    overlap_ratio = overlap / denom

    # If both questions share most key tokens, allow direct answer.
    # Otherwise we will fall back to faq_context + RAG.
    if overlap_ratio >= 0.55:
        return True

    # Additional structural hint: common short "intent" keywords.
    # If the intent differs strongly, deny direct.
    intent_tokens = {"как", "где", "почему", "сколько", "срок", "ошибка", "reset", "добавить", "удалить"}
    q_intent = bool(q_tokens & intent_tokens)
    f_intent = bool(f_tokens & intent_tokens)
    if q_intent and f_intent:
        # For intent-matching cases, require at least moderate overlap.
        return overlap_ratio >= 0.35

    # Default conservative path.
    return False


async def _async_fetch_top_faq_rows(
    *,
    tenant_id: uuid.UUID,
    question_embedding: list[float],
    db: AsyncSession,
    limit: int = 3,
) -> list[FAQRow]:
    """Async counterpart of :func:`_fetch_top_faq_rows`."""
    db_url = str(getattr(db.get_bind(), "url", ""))
    if "sqlite" in db_url:
        result = await db.execute(
            select(TenantFaq)
            .where(TenantFaq.tenant_id == tenant_id)
            .limit(max(limit * 5, limit))
        )
        rows = result.scalars().all()
        scored: list[FAQRow] = []
        for r in rows:
            vec = _parse_sqlite_vector_text(r.question_embedding)
            if vec is None:
                continue
            score = cosine_similarity(question_embedding, vec)
            scored.append(
                FAQRow(
                    id=r.id,
                    question=r.question,
                    answer=r.answer,
                    approved=bool(r.approved),
                    score=float(score),
                )
            )
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

    distance_expr = TenantFaq.question_embedding.cosine_distance(question_embedding)
    result = await db.execute(
        select(TenantFaq, distance_expr.label("distance"))
        .where(TenantFaq.tenant_id == tenant_id)
        .order_by(distance_expr)
        .limit(limit)
    )
    out: list[FAQRow] = []
    for faq, distance in result.all():
        try:
            sim = max(0.0, 1.0 - float(distance))
        except (TypeError, ValueError):
            sim = 0.0
        out.append(
            FAQRow(
                id=faq.id,
                question=faq.question,
                answer=faq.answer,
                approved=bool(faq.approved),
                score=float(sim),
            )
        )
    return out


def _classify_faq_match(
    *,
    rows: list[FAQRow],
    question: str,
) -> FAQMatchResult:
    """Pure post-fetch classifier shared by sync and async ``match_faq``."""
    direct_threshold, context_threshold, context_max_items = _faq_thresholds()

    if not rows:
        return FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="no_faq_candidates",
        )

    top = rows[0]
    top_score = top.score
    direct_guard_used = False
    direct_guard_passed = False

    approved_candidates = [r for r in rows if r.approved and r.score > direct_threshold]
    best_approved = approved_candidates[0] if approved_candidates else None
    promotion_delta = _approved_promotion_delta()
    promoted_approved = (
        best_approved is not None
        and best_approved.score >= (top_score - promotion_delta)
    )
    direct_candidate = best_approved if promoted_approved else top

    # High-confidence direct path: score + approved + guard.
    if direct_candidate.score > direct_threshold and direct_candidate.approved:
        direct_guard_used = True
        try:
            direct_guard_passed = bool(
                direct_applicability_guard(
                    question=question,
                    faq_question=direct_candidate.question,
                    faq_answer=direct_candidate.answer,
                )
            )
        except Exception:
            # Precision guard is a precision layer; degrade safely on any error.
            direct_guard_passed = False

        if direct_guard_passed:
            return FAQMatchResult(
                strategy="faq_direct",
                faq_items=[direct_candidate],
                top_score=top_score,
                selected_score=direct_candidate.score,
                selected_faq_id=str(direct_candidate.id),
                direct_guard_used=True,
                direct_guard_passed=True,
                decision_reason=(
                    "approved_promoted_high_score_guard_passed"
                    if promoted_approved and direct_candidate.id != top.id
                    else "approved_high_score_guard_passed"
                ),
            )

        # Approved but guard failed or was uncertain → context path.
        # In this phase we add only the top FAQ as a precision hint.
        return FAQMatchResult(
            strategy="faq_context",
            faq_items=[direct_candidate],
            top_score=top_score,
            selected_score=direct_candidate.score,
            selected_faq_id=str(direct_candidate.id),
            direct_guard_used=True,
            direct_guard_passed=False,
            decision_reason=(
                "approved_promoted_high_score_guard_failed_or_error"
                if promoted_approved and direct_candidate.id != top.id
                else "high_score_guard_failed_or_error"
            ),
        )

    # High-score (but not approved): context path with only top FAQ.
    if top_score > direct_threshold and not top.approved:
        return FAQMatchResult(
            strategy="faq_context",
            faq_items=[top],
            top_score=top_score,
            selected_score=top.score,
            selected_faq_id=str(top.id),
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="high_score_not_approved",
        )

    # Mid-score: context path with top-N hints.
    if top_score > context_threshold:
        context_items = [r for r in rows if r.score > context_threshold][:context_max_items]
        if not context_items:
            # Defensive fallback: treat as rag_only if nothing survives.
            return FAQMatchResult(
                strategy="rag_only",
                faq_items=[],
                top_score=top_score,
                selected_score=top.score,
                selected_faq_id=str(top.id),
                direct_guard_used=False,
                direct_guard_passed=False,
                decision_reason="no_context_items_after_threshold_filter",
            )
        return FAQMatchResult(
            strategy="faq_context",
            faq_items=context_items,
            top_score=top_score,
            selected_score=top.score,
            selected_faq_id=str(top.id),
            direct_guard_used=direct_guard_used,
            direct_guard_passed=direct_guard_passed,
            decision_reason="score_in_context_band",
        )

    # Low score: ignore FAQ completely.
    return FAQMatchResult(
        strategy="rag_only",
        faq_items=[],
        top_score=top_score,
        selected_score=top.score,
        selected_faq_id=str(top.id),
        direct_guard_used=False,
        direct_guard_passed=False,
        decision_reason="score_below_context_threshold",
    )


def match_faq(
    *,
    tenant_id: uuid.UUID,
    question: str,
    question_embedding: list[float],
    db: Session,
) -> FAQMatchResult:
    rows = _fetch_top_faq_rows(
        tenant_id=tenant_id,
        question_embedding=question_embedding,
        db=db,
        limit=3,
    )
    return _classify_faq_match(rows=rows, question=question)


async def async_match_faq(
    *,
    tenant_id: uuid.UUID,
    question: str,
    question_embedding: list[float],
    db: AsyncSession,
) -> FAQMatchResult:
    """Async counterpart of :func:`match_faq`."""
    rows = await _async_fetch_top_faq_rows(
        tenant_id=tenant_id,
        question_embedding=question_embedding,
        db=db,
        limit=3,
    )
    return _classify_faq_match(rows=rows, question=question)
