"""Two-level cache of generated chat answers.

Level 1 (exact) lives in Redis: the normalized question text, keyed together
with the bot, the response language and a knowledge-base fingerprint, maps to
the answer the pipeline produced for it. Level 2 (semantic) lives in Postgres
next to the other pgvector tables: the question embedding the pipeline
computes anyway is compared against the stored embeddings of the same scope,
and the nearest cached answer is served when its cosine similarity clears
``ANSWER_CACHE_SEMANTIC_THRESHOLD``. Plain Redis has no nearest-neighbour
search; pgvector is what the FAQ matcher already uses at the same point of
the pipeline.

Both levels are best-effort: a Redis or Postgres error is a miss, and
``ANSWER_CACHE_TTL_SECONDS=0`` disables lookups and writes.

The knowledge-base fingerprint folds in the tenant's document and FAQ rows,
the bot's instructions and disclosure config, and a Redis generation counter
bumped whenever embeddings are (re)written. Any change moves every key of the
scope, so a stale answer is never looked up again and simply expires with its
TTL — the TTL is the lower bound the fingerprint cannot cover (tenant profile
edits, quick answers).

What gets stored is decided by the pipeline (``steps/answer_cache.py``): only
self-contained, confident answers — first turn of a chat, no user context in
the prompt, no PII redaction on the question, strong retrieval and no
escalation / handoff / clarification signal from the model. Turns that depend
on session state (operator, escalation FSM, greetings) never reach the RAG
pipeline and so never touch the cache.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import redis as redis_mod
from backend.core.config import settings
from backend.models import AnswerCacheEntry, Document, TenantFaq
from backend.models.base import _utcnow
from backend.observability.cache_metrics import record_hit, record_miss
from backend.utils.math import cosine_similarity

logger = logging.getLogger(__name__)

EXACT_CACHE_NAME = "answer_exact"
SEMANTIC_CACHE_NAME = "answer_semantic"

AnswerCacheLevel = Literal["exact", "semantic"]

_PAYLOAD_VERSION = 1
_EXACT_KEY_PREFIX = "cache:answer:"
_GENERATION_KEY_PREFIX = "cache:answer_gen:"
_SEMANTIC_LOOKUP_TIMEOUT_SEC = 2.0


def is_enabled() -> bool:
    return settings.answer_cache_ttl_seconds > 0


def _strip_edge_punctuation(text: str) -> str:
    start, end = 0, len(text)
    while start < end and unicodedata.category(text[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(text[end - 1]).startswith("P"):
        end -= 1
    return text[start:end]


def normalize_question(text: str) -> str:
    """Casefolded, NFKC-normalized text with collapsed whitespace and no
    leading/trailing punctuation. Script-agnostic by construction."""
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    return " ".join(_strip_edge_punctuation(" ".join(folded.split())).split())


def question_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnswerCacheScope:
    """Everything a cached answer must agree on before it may be reused."""

    tenant_id: uuid.UUID
    bot_id: uuid.UUID | None
    kb_fingerprint: str
    response_language: str
    question: str
    question_hash: str

    @property
    def exact_key(self) -> str:
        material = "|".join(
            [
                str(self.tenant_id),
                str(self.bot_id or ""),
                self.kb_fingerprint,
                self.response_language,
                self.question_hash,
            ]
        )
        return _EXACT_KEY_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedAnswer:
    """Serialized outcome of one generated turn, enough to rebuild the
    pipeline result the handler consumes (citations, confidence, timings)."""

    answer: str
    strategy: str
    document_ids: tuple[str, ...]
    scores: tuple[float, ...]
    chunk_texts: tuple[str, ...]
    retrieval_mode: str
    best_rank_score: float | None
    best_confidence_score: float | None
    confidence_source: str
    reliability_score: str
    retrieval_ms: int
    llm_ms: int
    created_at: str

    @property
    def saved_ms(self) -> int:
        return self.retrieval_ms + self.llm_ms

    def to_dict(self) -> dict[str, Any]:
        return {"v": _PAYLOAD_VERSION, **asdict(self)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_payload(cls, raw: Any) -> CachedAnswer | None:
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else dict(raw)
        except (TypeError, ValueError):
            return None
        if data.get("v") != _PAYLOAD_VERSION:
            return None
        try:
            document_ids = tuple(str(uuid.UUID(str(d))) for d in data.get("document_ids") or [])
            return cls(
                answer=str(data["answer"]),
                strategy=str(data["strategy"]),
                document_ids=document_ids,
                scores=tuple(float(s) for s in data.get("scores") or []),
                chunk_texts=tuple(str(c) for c in data.get("chunk_texts") or []),
                retrieval_mode=str(data.get("retrieval_mode") or "hybrid"),
                best_rank_score=_optional_float(data.get("best_rank_score")),
                best_confidence_score=_optional_float(data.get("best_confidence_score")),
                confidence_source=str(data.get("confidence_source") or "none"),
                reliability_score=str(data.get("reliability_score") or "high"),
                retrieval_ms=int(data.get("retrieval_ms") or 0),
                llm_ms=int(data.get("llm_ms") or 0),
                created_at=str(data.get("created_at") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class AnswerCacheHit:
    level: AnswerCacheLevel
    similarity: float
    answer: CachedAnswer

    @property
    def saved_ms(self) -> int:
        return self.answer.saved_ms


@dataclass(frozen=True)
class AnswerCacheCandidate:
    """A freshly generated answer the handler may store once the turn commits."""

    scope: AnswerCacheScope
    question_embedding: list[float]
    answer: CachedAnswer


# ---------------------------------------------------------------------------
# Scope / fingerprint
# ---------------------------------------------------------------------------


def _generation_key(tenant_id: uuid.UUID) -> str:
    return f"{_GENERATION_KEY_PREFIX}{tenant_id}"


async def _knowledge_base_state(tenant_id: uuid.UUID, db: AsyncSession) -> list[str]:
    docs = Document.tenant_id == tenant_id
    faqs = TenantFaq.tenant_id == tenant_id
    row = (
        await db.execute(
            select(
                select(func.count(Document.id)).where(docs).scalar_subquery(),
                select(func.max(Document.updated_at)).where(docs).scalar_subquery(),
                select(func.count(TenantFaq.id)).where(faqs).scalar_subquery(),
                select(func.count(TenantFaq.id))
                .where(faqs, TenantFaq.approved.is_(True))
                .scalar_subquery(),
                select(func.max(TenantFaq.created_at)).where(faqs).scalar_subquery(),
            )
        )
    ).one()
    return [str(value) for value in row]


async def resolve_scope(
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID | None,
    response_language: str,
    question: str,
    agent_instructions: str | None,
    disclosure_config: dict[str, Any] | None,
    db: AsyncSession,
) -> AnswerCacheScope | None:
    """Build the cache scope for this turn, or ``None`` when the cache is off
    or the knowledge-base state cannot be read (treated as a miss)."""
    if not is_enabled():
        return None
    normalized = normalize_question(question)
    if not normalized:
        return None
    try:
        kb_state = await _knowledge_base_state(tenant_id, db)
    except Exception:
        logger.debug("answer_cache_kb_state_failed tenant_id=%s", tenant_id, exc_info=True)
        return None
    generation = await redis_mod.cache_get(_generation_key(tenant_id))
    material = json.dumps(
        [
            kb_state,
            agent_instructions or "",
            disclosure_config or {},
            generation or "0",
        ],
        sort_keys=True,
        default=str,
    )
    return AnswerCacheScope(
        tenant_id=tenant_id,
        bot_id=bot_id,
        kb_fingerprint=hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
        response_language=response_language,
        question=normalized,
        question_hash=question_hash(normalized),
    )


async def invalidate_tenant(tenant_id: uuid.UUID | None) -> None:
    """Move every key of the tenant by bumping its generation counter."""
    if tenant_id is None or not redis_mod.is_enabled():
        return
    await redis_mod.cache_incr(_generation_key(tenant_id))


def invalidate_tenant_sync(tenant_id: uuid.UUID | None) -> None:
    """Blocking :func:`invalidate_tenant` for sync services and worker threads."""
    if tenant_id is None or not redis_mod.is_enabled():
        return
    redis_mod.cache_incr_sync(_generation_key(tenant_id))


# ---------------------------------------------------------------------------
# Level 1 — exact match (Redis)
# ---------------------------------------------------------------------------


async def exact_get(scope: AnswerCacheScope) -> CachedAnswer | None:
    if not redis_mod.is_enabled():
        return None
    raw = await redis_mod.cache_get(scope.exact_key)
    cached = CachedAnswer.from_payload(raw) if raw else None
    if cached is None:
        record_miss(EXACT_CACHE_NAME)
        return None
    record_hit(EXACT_CACHE_NAME)
    return cached


async def promote_exact(scope: AnswerCacheScope, cached: CachedAnswer) -> None:
    """Write a semantic hit through to the exact level under this wording."""
    await redis_mod.cache_set_with_ttl(
        scope.exact_key, cached.to_json(), settings.answer_cache_ttl_seconds
    )


# ---------------------------------------------------------------------------
# Level 2 — nearest cached question (pgvector)
# ---------------------------------------------------------------------------


def _bound_db_url(db: AsyncSession) -> str:
    try:
        return str(db.get_bind().url)
    except Exception:
        return ""


def _vector_from_db(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw.strip())
        except (ValueError, SyntaxError):
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    try:
        return [float(v) for v in raw]
    except (TypeError, ValueError):
        return None


async def _safe_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        pass


async def _nearest_entry(
    scope: AnswerCacheScope,
    question_embedding: list[float],
    db: AsyncSession,
) -> tuple[AnswerCacheEntry, float] | None:
    in_scope = (
        AnswerCacheEntry.tenant_id == scope.tenant_id,
        AnswerCacheEntry.bot_id == scope.bot_id,
        AnswerCacheEntry.kb_fingerprint == scope.kb_fingerprint,
        AnswerCacheEntry.response_language == scope.response_language,
        AnswerCacheEntry.expires_at > _utcnow(),
    )
    if "sqlite" in _bound_db_url(db):
        rows = (await db.execute(select(AnswerCacheEntry).where(*in_scope))).scalars().all()
        best: tuple[AnswerCacheEntry, float] | None = None
        for row in rows:
            vector = _vector_from_db(row.question_embedding)
            if vector is None:
                continue
            similarity = float(cosine_similarity(question_embedding, vector))
            if best is None or similarity > best[1]:
                best = (row, similarity)
        return best

    distance = AnswerCacheEntry.question_embedding.cosine_distance(question_embedding)
    row = (
        await db.execute(
            select(AnswerCacheEntry, distance.label("distance"))
            .where(*in_scope)
            .order_by(distance)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    entry, raw_distance = row
    return entry, max(0.0, 1.0 - float(raw_distance))


async def semantic_get(
    scope: AnswerCacheScope,
    question_embedding: list[float],
    db: AsyncSession | None,
) -> tuple[CachedAnswer, float] | None:
    """Nearest cached answer of the scope, if it clears the similarity threshold."""
    if db is None or not question_embedding:
        return None
    try:
        match = await asyncio.wait_for(
            _nearest_entry(scope, question_embedding, db),
            timeout=_SEMANTIC_LOOKUP_TIMEOUT_SEC,
        )
    except TimeoutError:
        logger.warning("answer_cache_semantic_lookup_timeout tenant_id=%s", scope.tenant_id)
        await _safe_rollback(db)
        record_miss(SEMANTIC_CACHE_NAME)
        return None
    except Exception:
        logger.debug("answer_cache_semantic_lookup_failed", exc_info=True)
        await _safe_rollback(db)
        record_miss(SEMANTIC_CACHE_NAME)
        return None
    if match is None or match[1] < settings.answer_cache_semantic_threshold:
        record_miss(SEMANTIC_CACHE_NAME)
        return None
    cached = CachedAnswer.from_payload(match[0].payload)
    if cached is None:
        record_miss(SEMANTIC_CACHE_NAME)
        return None
    record_hit(SEMANTIC_CACHE_NAME)
    return cached, match[1]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def store(candidate: AnswerCacheCandidate, *, db: AsyncSession | None) -> None:
    """Write the answer to both levels. Never raises: either level failing is
    logged and the turn is unaffected."""
    if not is_enabled():
        return
    ttl = settings.answer_cache_ttl_seconds
    scope = candidate.scope
    await redis_mod.cache_set_with_ttl(scope.exact_key, candidate.answer.to_json(), ttl)
    if db is None or not candidate.question_embedding:
        return
    now = _utcnow()
    try:
        # Opportunistic purge keeps the scope scan small: expired rows of the
        # tenant and rows of this bot written under a superseded fingerprint.
        await db.execute(
            delete(AnswerCacheEntry).where(
                AnswerCacheEntry.tenant_id == scope.tenant_id,
                or_(
                    AnswerCacheEntry.expires_at <= now,
                    (AnswerCacheEntry.bot_id == scope.bot_id)
                    & (AnswerCacheEntry.kb_fingerprint != scope.kb_fingerprint),
                ),
            )
        )
        db.add(
            AnswerCacheEntry(
                tenant_id=scope.tenant_id,
                bot_id=scope.bot_id,
                kb_fingerprint=scope.kb_fingerprint,
                response_language=scope.response_language,
                question_hash=scope.question_hash,
                question=scope.question,
                question_embedding=list(candidate.question_embedding),
                payload=candidate.answer.to_dict(),
                created_at=now,
                expires_at=now + timedelta(seconds=ttl),
            )
        )
        await db.commit()
    except Exception:
        logger.debug("answer_cache_store_failed tenant_id=%s", scope.tenant_id, exc_info=True)
        await _safe_rollback(db)
