"""Phase 4 — Chat-log analysis job.

Clusters recent user messages per client, extracts FAQ candidates and aliases.

Key design decisions (v2.0 arch-review fixes):
- O(N*K) clustering: similarity search constrained to current batch only
- Dual watermark: primary = last_run_started_at (timestamp), auxiliary = last_processed_id
- Backpressure: embedding batching with delays + LLM semaphore + job timeout
- Alias pre-filter: cluster_size >= 5 AND lexical_diversity > 0.6
- Answer selection: next assistant message after the specific user message
- Algorithm versioning: CURRENT_ANALYSIS_VERSION drives cache invalidation
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.models import (
    LogAnalysisState,
    Message,
    MessageEmbedding,
    MessageFeedback,
    MessageRole,
    TenantFaq,
)

logger = logging.getLogger(__name__)

CURRENT_ANALYSIS_VERSION = 1

EMBEDDING_MODEL = "text-embedding-3-small"

# ── LLM concurrency guard ────────────────────────────────────────────────────
_LLM_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _LLM_SEMAPHORE
    if _LLM_SEMAPHORE is None:
        _LLM_SEMAPHORE = asyncio.Semaphore(3)
    return _LLM_SEMAPHORE


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class MessageRow:
    id: uuid.UUID
    content: str
    created_at: datetime
    conversation_id: uuid.UUID  # chat_id in our schema
    embedding: list[float] | None = field(default=None, repr=False)


@dataclass
class ClusterMember:
    message: MessageRow
    answer: str | None
    has_thumbs_up: bool
    feedback: MessageFeedback = MessageFeedback.none


# ── Embedding helpers ─────────────────────────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    n1 = math.sqrt(sum(x * x for x in a))
    n2 = math.sqrt(sum(y * y for y in b))
    if n1 == 0 or n2 == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (n1 * n2)))


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    result = [0.0] * dim
    for v in vectors:
        for i, val in enumerate(v):
            result[i] += val
    n = len(vectors)
    return [x / n for x in result]


# ── Database helpers ──────────────────────────────────────────────────────────

def _get_or_create_state(db: Session, client_id: uuid.UUID) -> LogAnalysisState:
    state = db.query(LogAnalysisState).filter_by(tenant_id=client_id).first()
    if state is None:
        state = LogAnalysisState(tenant_id=client_id)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def _load_messages(
    db: Session,
    client_id: uuid.UUID,
    last_run_started_at: datetime | None,
    batch_size: int,
) -> list[MessageRow]:
    """Load user messages since last run, excluding the last 30 s (delayed-insert guard)."""
    from backend.models import Chat

    cutoff = last_run_started_at or datetime(1970, 1, 1, tzinfo=UTC)
    # 30-second guard against delayed inserts — computed in Python for SQLite compat
    upper_bound = datetime.now(UTC) - timedelta(seconds=30)

    rows = (
        db.query(Message)
        .join(Chat, Chat.id == Message.chat_id)
        .filter(
            Chat.client_id == client_id,
            Message.role == MessageRole.user,
            Message.created_at > cutoff,
            Message.created_at < upper_bound,
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
        .limit(batch_size)
        .all()
    )
    return [
        MessageRow(
            id=m.id,
            content=m.content,
            created_at=m.created_at,
            conversation_id=m.chat_id,
        )
        for m in rows
    ]


def _get_answer_for_message(
    db: Session,
    msg_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> tuple[str | None, MessageFeedback]:
    """Return (answer_text, assistant_feedback) for the next assistant message after msg_id.

    Feedback is read from the ASSISTANT message, not the user message — thumbs
    up/down is only written on assistant turns in the chat API.
    """
    user_msg = db.query(Message).filter(Message.id == msg_id).first()
    if user_msg is None:
        return None, MessageFeedback.none

    assistant_msg = (
        db.query(Message)
        .filter(
            Message.chat_id == conversation_id,
            Message.role == MessageRole.assistant,
            Message.created_at > user_msg.created_at,
        )
        .order_by(Message.created_at.asc())
        .first()
    )
    if assistant_msg is None:
        return None, MessageFeedback.none

    return assistant_msg.content, assistant_msg.feedback


def _get_cached_embeddings(
    db: Session,
    client_id: uuid.UUID,
    message_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[float]]:
    """Fetch existing embeddings from message_embeddings table."""
    if not message_ids:
        return {}
    rows = (
        db.query(MessageEmbedding)
        .filter(
            MessageEmbedding.tenant_id == client_id,
            MessageEmbedding.message_id.in_(message_ids),
        )
        .all()
    )
    result: dict[uuid.UUID, list[float]] = {}
    for r in rows:
        raw = r.embedding
        if raw is None:
            continue
        if isinstance(raw, list):
            result[r.message_id] = [float(x) for x in raw]
        elif isinstance(raw, str):
            import json
            try:
                result[r.message_id] = [float(x) for x in json.loads(raw)]
            except Exception:
                pass
    return result


def _save_embeddings(
    db: Session,
    client_id: uuid.UUID,
    batch: list[MessageRow],
    vectors: list[list[float]],
) -> None:
    for msg, vec in zip(batch, vectors, strict=True):
        existing = db.query(MessageEmbedding).filter_by(message_id=msg.id).first()
        if existing:
            existing.last_used_at = datetime.now(UTC)
        else:
            db.add(
                MessageEmbedding(
                    message_id=msg.id,
                    tenant_id=client_id,
                    embedding=vec,
                )
            )
    db.commit()


def _touch_embeddings(
    db: Session,
    message_ids: list[uuid.UUID],
) -> None:
    """Update last_used_at for embeddings that were used in this job run."""
    if not message_ids:
        return
    db.query(MessageEmbedding).filter(
        MessageEmbedding.message_id.in_(message_ids)
    ).update(
        {"last_used_at": datetime.now(UTC)},
        synchronize_session=False,
    )
    db.commit()


# ── Embedding generation (throttled) ─────────────────────────────────────────

async def _generate_embeddings(
    messages: list[MessageRow],
    api_key: str,
    db: Session,
    client_id: uuid.UUID,
) -> None:
    """Fetch embeddings for messages that don't have one yet. Updates MessageRow in-place."""
    # Load from cache first
    all_ids = [m.id for m in messages]
    cached = _get_cached_embeddings(db, client_id, all_ids)
    for msg in messages:
        if msg.id in cached:
            msg.embedding = cached[msg.id]

    missing = [m for m in messages if m.embedding is None]
    if not missing:
        return

    client = get_openai_client(api_key)
    batch_size = settings.embedding_batch_size
    delay = settings.embedding_batch_delay_sec

    for i in range(0, len(missing), batch_size):
        batch = missing[i: i + batch_size]
        resp = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=[m.content for m in batch],
        )
        vectors = [item.embedding for item in resp.data]
        for msg, vec in zip(batch, vectors, strict=True):
            msg.embedding = vec
        _save_embeddings(db, client_id, batch, vectors)
        if i + batch_size < len(missing):
            await asyncio.sleep(delay)

    # Touch all used embeddings
    _touch_embeddings(db, all_ids)


# ── Clustering ────────────────────────────────────────────────────────────────

def _cluster_messages(
    messages: list[MessageRow],
) -> list[list[MessageRow]]:
    """Greedy cosine-similarity clustering, constrained to current batch.

    Complexity: O(N * K) where K is avg cluster size.
    """
    threshold = settings.log_cluster_similarity_threshold
    min_size = settings.log_cluster_min_size

    assigned: set[uuid.UUID] = set()
    clusters: list[list[MessageRow]] = []

    for msg in messages:
        if msg.id in assigned or msg.embedding is None:
            continue

        cluster: list[MessageRow] = [msg]
        assigned.add(msg.id)

        for other in messages:
            if other.id in assigned or other.embedding is None:
                continue
            if _cosine_similarity(msg.embedding, other.embedding) >= threshold:
                cluster.append(other)
                assigned.add(other.id)

        if len(cluster) >= min_size:
            clusters.append(cluster)

    return clusters


def _representative_question(cluster: list[MessageRow]) -> MessageRow:
    """Pick the message closest to the centroid of the cluster."""
    vectors = [m.embedding for m in cluster if m.embedding is not None]
    if not vectors:
        return cluster[0]
    c = _centroid(vectors)
    best = max(cluster, key=lambda m: _cosine_similarity(m.embedding or [], c))
    return best


# ── FAQ confidence ────────────────────────────────────────────────────────────

def _calculate_confidence(cluster_size: int, has_thumbs_up: bool) -> float:
    base = min(0.5 + (cluster_size - settings.log_cluster_min_size) * 0.05, 0.8)
    if has_thumbs_up:
        base = min(base + 0.15, 0.95)
    return round(base, 2)


# ── Deduplication (reuses existing faq_service logic) ────────────────────────

def _find_existing_faq(
    db: Session,
    client_id: uuid.UUID,
    question_embedding: list[float],
) -> TenantFaq | None:
    """Return existing FAQ with cosine similarity >= threshold, or None."""
    from backend.tenant_knowledge.faq_service import DEDUP_SIMILARITY_THRESHOLD
    try:
        distance_expr = TenantFaq.question_embedding.cosine_distance(question_embedding)
        row = (
            db.query(TenantFaq, distance_expr.label("distance"))
            .filter(TenantFaq.tenant_id == client_id)
            .filter(TenantFaq.question_embedding.isnot(None))
            .order_by(distance_expr)
            .limit(1)
            .first()
        )
        if not row:
            return None
        faq, distance = row
        similarity = max(0.0, 1.0 - float(distance))
        if similarity >= DEDUP_SIMILARITY_THRESHOLD:
            return faq
    except Exception:
        pass
    return None


def _create_faq_candidate(
    db: Session,
    client_id: uuid.UUID,
    representative: MessageRow,
    best_member: ClusterMember,
    cluster_members: list[MessageRow],
    api_key: str,
) -> bool:
    """Embed question, dedup, then insert. Returns True if created."""
    client = get_openai_client(api_key)
    question = representative.content.strip()
    answer = (best_member.answer or "").strip()
    if not question or not answer:
        return False

    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=question)
    q_emb = resp.data[0].embedding

    existing = _find_existing_faq(db, client_id, q_emb)
    has_thumbs_up = best_member.has_thumbs_up

    # Dedup logic per spec:
    # >= 0.92 + pending → skip; >= 0.92 + approved → create (client compares)
    if existing is not None:
        if not existing.approved:
            return False  # duplicate pending — skip

    cluster_size = len(cluster_members)
    confidence = _calculate_confidence(cluster_size, has_thumbs_up)
    approved = confidence >= settings.faq_confidence_auto_accept

    db.add(
        TenantFaq(
            tenant_id=client_id,
            question=question,
            answer=answer,
            question_embedding=q_emb,
            confidence=confidence,
            source="logs",
            approved=approved,
            cluster_size=cluster_size,
            source_message_ids=[str(m.id) for m in cluster_members[:10]],
        )
    )
    db.commit()
    return True


# ── Idempotency / lock ────────────────────────────────────────────────────────

def try_acquire_job_lock(
    db: Session,
    client_id: uuid.UUID,
) -> tuple[datetime, datetime | None] | None:
    """Atomically set is_running=True.

    Returns (job_start_time, old_watermark) on success, or None if already running.

    The watermark (last_run_started_at) is NOT advanced here — it is written
    in _finalize_job based on the last message actually processed, so that a
    partial batch or early exit does not skip messages that were never loaded.
    """
    from sqlalchemy import update as sa_update

    state = _get_or_create_state(db, client_id)
    if state.is_running:
        return None

    old_watermark = state.last_run_started_at
    now = datetime.now(UTC)

    result = db.execute(
        sa_update(LogAnalysisState)
        .where(
            LogAnalysisState.tenant_id == client_id,
            LogAnalysisState.is_running == False,  # noqa: E712
        )
        .values(is_running=True)
        .returning(LogAnalysisState.tenant_id)
    )
    db.commit()
    row = result.fetchone()
    if row is None:
        return None  # race — another process grabbed the lock
    return now, old_watermark


def enqueue_log_analysis_job(
    db: Session,
    client_id: uuid.UUID,
    api_key: str,
    trigger: str = "manual",
) -> bool:
    """Acquire lock and launch job in a daemon thread with its own DB session.

    Returns True if job was started, False if already running.
    """
    _get_or_create_state(db, client_id)
    result = try_acquire_job_lock(db, client_id)
    if result is None:
        logger.debug("Log analysis already running for client %s", client_id)
        return False

    job_start, old_watermark = result

    import threading
    t = threading.Thread(
        target=_run_job_sync,
        args=(client_id, api_key, job_start, old_watermark, trigger),
        daemon=True,
    )
    t.start()
    return True


# ── Main job ─────────────────────────────────────────────────────────────────

def _run_job_sync(
    client_id: uuid.UUID,
    api_key: str,
    job_start: datetime,
    old_watermark: datetime | None,
    trigger: str,
) -> None:
    """Entry point for daemon thread — opens its own DB session, runs async job."""
    from backend.core.db import SessionLocal

    db = SessionLocal()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            run_job(client_id, api_key, db, job_start, old_watermark, trigger)
        )
    finally:
        loop.close()
        db.close()


async def run_job(
    client_id: uuid.UUID,
    api_key: str,
    db: Session,
    job_start: datetime,
    old_watermark: datetime | None,
    trigger: str = "manual",
) -> None:
    """Core analysis job: load messages → embed → cluster → FAQ → aliases.

    old_watermark is the previous run's last_run_started_at — used as the
    lower bound for loading messages in THIS run.  job_start (already written
    to last_run_started_at) becomes the watermark for the NEXT run.
    """
    from backend.jobs.alias_extractor import extract_and_merge_aliases

    faq_count = 0
    alias_count = 0
    status = "ok"
    last_msg_id: uuid.UUID | None = None
    last_msg_created_at: datetime | None = None
    job_started_mono = time.monotonic()

    try:
        # ── Version check ────────────────────────────────────────────────────
        state = _get_or_create_state(db, client_id)
        if state.analysis_version != CURRENT_ANALYSIS_VERSION:
            logger.info(
                "Analysis version changed (%s→%s), resetting watermark for client %s",
                state.analysis_version,
                CURRENT_ANALYSIS_VERSION,
                client_id,
            )
            from sqlalchemy import update as sa_update
            db.execute(
                sa_update(LogAnalysisState)
                .where(LogAnalysisState.tenant_id == client_id)
                .values(
                    last_run_started_at=None,
                    last_processed_id=None,
                    analysis_version=CURRENT_ANALYSIS_VERSION,
                )
            )
            db.commit()
            old_watermark = None  # full reprocess

        # ── Load messages using the PREVIOUS watermark ───────────────────────
        messages = _load_messages(
            db,
            client_id,
            old_watermark,
            settings.log_analysis_batch_size,
        )
        if len(messages) < settings.log_cluster_min_size:
            status = "skipped_no_data"
            return

        # ── Generate embeddings (throttled) ──────────────────────────────────
        await _generate_embeddings(messages, api_key, db, client_id)

        # ── Cluster (batch-constrained) ──────────────────────────────────────
        clusters = _cluster_messages(messages)

        # Limit to top MAX_FAQ_PER_RUN by cluster size
        clusters.sort(key=lambda c: len(c), reverse=True)
        clusters = clusters[: settings.max_faq_per_run]

        # ── Per-cluster: pick best answer, create FAQ, collect aliases ───────
        alias_inputs: list[list[str]] = []

        for cluster in clusters:
            if time.monotonic() - job_started_mono > settings.max_job_duration_sec:
                logger.warning(
                    "Job timeout, stopping early for client %s", client_id
                )
                break

            # Collect answers — feedback read from assistant messages only
            members: list[ClusterMember] = []
            for msg in cluster:
                answer_text, feedback = _get_answer_for_message(
                    db, msg.id, msg.conversation_id
                )
                if answer_text:
                    members.append(
                        ClusterMember(
                            message=msg,
                            answer=answer_text,
                            has_thumbs_up=(feedback == MessageFeedback.up),
                            feedback=feedback,
                        )
                    )

            if not members:
                continue

            # Skip cluster if ALL assistant answers are thumbs-down
            if all(m.feedback == MessageFeedback.down for m in members):
                continue

            # Best answer: thumbs_up > no feedback (none) > skip thumbs_down
            best = next((m for m in members if m.has_thumbs_up), None)
            if best is None:
                best = next(
                    (m for m in members if m.feedback != MessageFeedback.down), None
                )
            if best is None:
                continue

            representative = _representative_question(cluster)
            created = _create_faq_candidate(
                db, client_id, representative, best, cluster, api_key
            )
            if created:
                faq_count += 1

            # Prepare alias extraction input
            alias_inputs.append([m.message.content for m in members])

        # ── Alias extraction (async, throttled) ──────────────────────────────
        alias_count = await extract_and_merge_aliases(
            db=db,
            client_id=client_id,
            cluster_questions_list=alias_inputs,
            api_key=api_key,
        )

        if messages:
            last_msg_id = messages[-1].id
            last_msg_created_at = messages[-1].created_at

    except Exception as exc:
        status = "failed"
        logger.error("Log analysis job failed for client %s: %s", client_id, exc, exc_info=True)

    finally:
        _finalize_job(
            db, client_id, last_msg_id, last_msg_created_at,
            faq_count, alias_count, status, job_start,
        )


def _finalize_job(
    db: Session,
    client_id: uuid.UUID,
    last_msg_id: uuid.UUID | None,
    last_msg_created_at: datetime | None,
    faq_count: int,
    alias_count: int,
    status: str,
    job_start: datetime,
) -> None:
    from sqlalchemy import update as sa_update

    # Advance the watermark only as far as messages were actually processed.
    # If the batch was partial or the job exited early, this ensures the next
    # run resumes from the last processed timestamp rather than skipping the
    # remainder of the backlog.  When nothing was processed (empty/skipped),
    # advance to job_start so the next run does not re-scan an empty range.
    new_watermark = last_msg_created_at if last_msg_created_at is not None else job_start

    values: dict = {
        "is_running": False,
        "last_run_at": datetime.now(UTC),
        "last_run_started_at": new_watermark,
        "messages_since_last_run": 0,
        "last_run_status": status,
        "last_run_faq_created": faq_count,
        "last_run_aliases_created": alias_count,
        "analysis_version": CURRENT_ANALYSIS_VERSION,
    }
    if last_msg_id is not None:
        values["last_processed_id"] = last_msg_id

    try:
        db.execute(
            sa_update(LogAnalysisState)
            .where(LogAnalysisState.tenant_id == client_id)
            .values(**values)
        )
        db.commit()
    except Exception:
        logger.exception("Failed to finalize log analysis job state for client %s", client_id)
        db.rollback()


# ── Threshold trigger helper ──────────────────────────────────────────────────

def increment_and_check_threshold(
    client_id: uuid.UUID,
    api_key: str,
) -> None:
    """Increment messages_since_last_run; enqueue job if threshold reached.

    Opens its own DB session — safe to call from any background thread.
    """
    from sqlalchemy import update as sa_update

    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        _get_or_create_state(db, client_id)

        db.execute(
            sa_update(LogAnalysisState)
            .where(LogAnalysisState.tenant_id == client_id)
            .values(
                messages_since_last_run=LogAnalysisState.messages_since_last_run + 1
            )
        )
        db.commit()

        state = _get_or_create_state(db, client_id)
        threshold = settings.log_analysis_threshold_messages
        if state.messages_since_last_run >= threshold and not state.is_running:
            enqueue_log_analysis_job(
                db=db,
                client_id=client_id,
                api_key=api_key,
                trigger="threshold",
            )
    except Exception:
        logger.exception(
            "increment_and_check_threshold failed for client %s", client_id
        )
    finally:
        db.close()


# ── Retention cron ────────────────────────────────────────────────────────────

def run_embedding_retention(db: Session) -> int:
    """Delete message embeddings older than retention window.

    Should be called once daily from a cron/scheduler.
    Returns number of deleted rows.
    """
    from datetime import timedelta

    from sqlalchemy import delete as sa_delete

    cutoff = datetime.now(UTC) - timedelta(
        days=settings.log_embeddings_retention_days
    )
    result = db.execute(
        sa_delete(MessageEmbedding).where(MessageEmbedding.last_used_at < cutoff)
    )
    db.commit()
    deleted = result.rowcount or 0
    logger.info("Retention: deleted %d stale message embeddings", deleted)
    return deleted
