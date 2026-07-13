"""Injection detector v2 — language-agnostic, two-level detection.

Level 1: Structural patterns (sync, ~0 ms, no API calls).
Level 2: Semantic embedding similarity (async with timeout, ~50-100 ms).

Any level triggering → immediate reject; subsequent levels are skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from time import monotonic, perf_counter

from backend.core.config import settings
from backend.guards.types import Verdict, VerdictReason
from backend.observability import TraceHandle, record_stage_ms
from backend.observability.cache_metrics import record_hit, record_miss
from backend.search.service import (
    async_embed_queries,
    async_embed_query,
    cosine_similarity,
)

logger = logging.getLogger(__name__)

# In-process cache name for the semantic-injection Redis cache (surfaced via the
# admin cache-metrics endpoint alongside the relevance-guard cache).
_SEMANTIC_CACHE_NAME = "injection_semantic"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InjectionDetectionResult:
    detected: bool
    level: int | None = None          # 1 or 2
    method: str | None = None         # 'structural' | 'semantic'
    pattern: str | None = None        # matched regex (level 1)
    score: float | None = None        # cosine similarity (level 2)
    normalized_input: str = ""


# ---------------------------------------------------------------------------
# Normalization (mandatory before any check)
# ---------------------------------------------------------------------------

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff\u00ad]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Level 1 — structural patterns
# ---------------------------------------------------------------------------

STRUCTURAL_PATTERNS: list[str] = [
    # Pseudo-system blocks (brackets, XML, markdown, fences).
    # Trailing \\b avoids prefix hits (e.g. systemd); heading lookahead avoids
    # benign titles like "### system requirements".
    r"\[\s*(system|admin|root|operator|developer|instruction|prompt)\s*\]",
    r"<\s*(system|admin|prompt|instruction|override)\b\s*[/>]",
    r"#{1,6}\s*(system|instruction|prompt|override|admin)\b(?=\s*$|\s*[#:\[\(\n])",
    r"---+\s*(system|reset|new.?prompt|override)\b\s*---+",
    r"```\s*(system|prompt|instruction|admin)\b",
    # Context reset directives — require "your" to target the AI explicitly.
    # Broad phrases like "new conversation" or "reset my history" are excluded
    # because they appear frequently in benign product-help queries; semantic
    # detection (level 2) handles injection attempts that use natural language.
    r"\b(?:reset|clear)\s+your\s+(context|history|instructions?)\b",
]

LEET_PATTERNS: list[str] = [
    r"(?!system)[5s][y][5s][t3][e3m][m]",   # system (excludes literal word)
    r"(?!admin)[@4a][d][m][1!i][n]",         # admin (excludes literal word)
    r"(?!prompt)[p][r][o0][m][p][t]",        # prompt (excludes literal word)
]

_ALL_STRUCTURAL = STRUCTURAL_PATTERNS + LEET_PATTERNS
_COMPILED_STRUCTURAL: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), p) for p in _ALL_STRUCTURAL
]


def detect_injection_structural(text: str) -> InjectionDetectionResult:
    normalized = normalize(text)
    for rx, pattern in _COMPILED_STRUCTURAL:
        if rx.search(normalized):
            return InjectionDetectionResult(
                detected=True,
                level=1,
                method="structural",
                pattern=pattern,
                score=None,
                normalized_input=normalized,
            )
    return InjectionDetectionResult(
        detected=False,
        level=None,
        method=None,
        pattern=None,
        score=None,
        normalized_input=normalized,
    )


# ---------------------------------------------------------------------------
# Level 2 — semantic embedding similarity
# ---------------------------------------------------------------------------

_reference_embeddings: list[list[float]] | None = None
_async_seed_lock = asyncio.Lock()


async def _get_reference_embeddings_async(api_key: str) -> list[list[float]]:
    """Async lazy-init of seed embeddings — safe for concurrent coroutines."""
    global _reference_embeddings
    async with _async_seed_lock:
        if _reference_embeddings is None:
            from backend.guards.injection_seeds import INJECTION_SEEDS

            _reference_embeddings = await async_embed_queries(
                INJECTION_SEEDS,
                api_key=api_key,
                timeout=settings.injection_semantic_timeout_sec,
            )
    return _reference_embeddings


def _reset_reference_embeddings() -> None:
    """For testing only — clear cached reference embeddings."""
    global _reference_embeddings
    _reference_embeddings = None


# ---------------------------------------------------------------------------
# Circuit breaker for the semantic (embedding-backed) level
# ---------------------------------------------------------------------------
# Follows the relevance guard's breaker shape: when the embedding service is
# unreachable, every turn otherwise pays the full ``injection_semantic_timeout_sec``
# (~2 s) waiting for level 2 to time out. After CIRCUIT_BREAKER_THRESHOLD
# consecutive embedding failures (timeouts / errors) the circuit opens and
# level 2 is skipped (pass-through) for CIRCUIT_HALF_OPEN_AFTER_SECONDS; after
# the cooldown one probe request is allowed through (on success the circuit
# closes, on failure the timer resets). Level 1 (structural) always keeps gating.
#
# Unlike the relevance guard, the breaker is keyed PER OpenAI API KEY (i.e. per
# tenant), not process-global. Level 2 is a security control: a global breaker
# would let one tenant's bad/expired key (5 embedding failures) disable
# natural-language injection detection for every other tenant on the worker for
# the whole cooldown. Per-key scoping confines the fail-open to the tenant whose
# key is actually failing; a genuine provider-wide outage still trips each
# tenant's breaker independently after its own failures.
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_HALF_OPEN_AFTER_SECONDS = 60.0
# Bound on the number of distinct keys tracked at once (only currently-failing
# keys hold state — success drops the entry), so a churn of bad keys can't grow
# the map without limit.
_CB_MAX_KEYS = 4096


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    circuit_opened_at: float | None = None
    # Wall-clock-ish ordering token for eviction (monotonic seconds of last touch).
    last_touch: float = field(default=0.0)


_cb_lock = threading.Lock()
_cb_states: dict[str, _BreakerState] = {}


def _cb_key(api_key: str) -> str:
    """Per-tenant breaker bucket. Hash so raw secrets aren't held as map keys."""
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]


def _circuit_is_open(api_key: str) -> bool:
    """True while this key's breaker is open (level 2 should be skipped)."""
    key = _cb_key(api_key)
    with _cb_lock:
        st = _cb_states.get(key)
        if st is None or st.consecutive_failures < CIRCUIT_BREAKER_THRESHOLD:
            return False
        now = monotonic()
        if st.circuit_opened_at is None:
            st.circuit_opened_at = now
        if now - st.circuit_opened_at < CIRCUIT_HALF_OPEN_AFTER_SECONDS:
            return True
        # Half-open: reset timer so only one probe gets through at a time.
        st.circuit_opened_at = None
        return False


def _record_semantic_failure(api_key: str) -> None:
    key = _cb_key(api_key)
    now = monotonic()
    with _cb_lock:
        st = _cb_states.get(key)
        if st is None:
            if len(_cb_states) >= _CB_MAX_KEYS:
                # Evict the least-recently-touched breaker to stay bounded.
                oldest = min(_cb_states, key=lambda k: _cb_states[k].last_touch)
                _cb_states.pop(oldest, None)
            st = _BreakerState()
            _cb_states[key] = st
        st.consecutive_failures += 1
        st.circuit_opened_at = now
        st.last_touch = now


def _record_semantic_success(api_key: str) -> None:
    # A closed breaker needs no state; dropping the entry keeps the map small.
    with _cb_lock:
        _cb_states.pop(_cb_key(api_key), None)


def _reset_circuit_breaker() -> None:
    """For testing only — clear all breaker state."""
    with _cb_lock:
        _cb_states.clear()


def _passthrough_result(normalized: str) -> InjectionDetectionResult:
    """Level-2 non-detection result (open circuit or timeout/error)."""
    return InjectionDetectionResult(
        detected=False,
        level=None,
        method=None,
        pattern=None,
        score=None,
        normalized_input=normalized,
    )


# ---------------------------------------------------------------------------
# Verdict cache for the (expensive) semantic level
# ---------------------------------------------------------------------------
# The semantic level is an embedding HTTP call on every turn. Repeated identical
# messages (spam, demo scripts) recompute the same verdict, so the L2 result is
# cached in Redis keyed by ``hash(tenant_id, guard_kind, normalized_input)``.
# The structural level (L1) is a regex sweep and not worth caching.
#
# The semantic verdict depends only on the normalized text (the injection seeds
# and threshold are global) — dialog context and tenant-profile version, which
# the relevance-guard cache folds into its key, are not inputs here, so they are
# deliberately absent from this key. Graceful: when Redis is unset/unreachable
# the core helpers return miss/False and detection runs directly.


def _semantic_cache_key(tenant_id: str, normalized: str) -> str:
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"guard:verdict:{VerdictReason.INJECTION_SEMANTIC.value}:{tenant_id}:{digest}"


def _emit_semantic_cache_metric(tenant_id: str, cache_hit: bool) -> None:
    """Mirror the relevance guard: emit a per-turn cache hit/miss signal."""
    try:
        from backend.observability.metrics import capture_event

        capture_event(
            "injection_semantic.cache",
            distinct_id=tenant_id,
            tenant_id=tenant_id,
            properties={"cache_hit": cache_hit},
        )
    except Exception:
        pass


async def _semantic_cache_get(
    key: str, normalized: str
) -> InjectionDetectionResult | None:
    from backend.core import redis as redis_mod

    raw = await redis_mod.cache_get(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    detected = bool(data.get("d"))
    return InjectionDetectionResult(
        detected=detected,
        level=2 if detected else None,
        method="semantic" if detected else None,
        pattern=None,
        score=data.get("s"),
        normalized_input=normalized,
    )


async def _semantic_cache_set(key: str, result: InjectionDetectionResult) -> None:
    from backend.core import redis as redis_mod

    payload = json.dumps({"d": result.detected, "s": result.score})
    await redis_mod.cache_set_with_ttl(
        key, payload, settings.guard_semantic_cache_ttl_seconds
    )


async def async_detect_injection_semantic(
    text: str,
    normalized: str,
    *,
    api_key: str,
    tenant_id: str | None = None,
) -> InjectionDetectionResult:
    """Level 2: semantic similarity with injection seeds (async).

    Uses ``async_embed_query`` with the OpenAI HTTP client timeout
    (``INJECTION_SEMANTIC_TIMEOUT_SEC``) so requests are cancelled at the
    transport layer and the event loop is not blocked during the embedding
    HTTP call. On timeout or error → pass-through (detected=False). After
    repeated failures the per-key circuit breaker opens and this level is
    skipped entirely (also pass-through) so a down embedding service does
    not add the timeout to every turn.

    When ``tenant_id`` is given and Redis is configured, the verdict is served
    from / written to the per-tenant verdict cache. The cache is consulted
    *before* the circuit breaker so a previously-computed verdict is still
    served (no embedding call needed) even while the breaker is open.
    """
    from backend.core import redis as redis_mod

    cache_enabled = tenant_id is not None and redis_mod.is_enabled()
    cache_key = _semantic_cache_key(tenant_id, normalized) if cache_enabled else None

    if cache_key is not None:
        cached = await _semantic_cache_get(cache_key, normalized)
        if cached is not None:
            record_hit(_SEMANTIC_CACHE_NAME)
            _emit_semantic_cache_metric(tenant_id, True)
            return cached
        record_miss(_SEMANTIC_CACHE_NAME)
        _emit_semantic_cache_metric(tenant_id, False)

    if _circuit_is_open(api_key):
        return _passthrough_result(normalized)
    try:
        embedding = await async_embed_query(
            text,
            api_key=api_key,
            timeout=settings.injection_semantic_timeout_sec,
            max_attempts=1,
        )
        ref_embeddings = await _get_reference_embeddings_async(api_key)
        max_score = max(
            cosine_similarity(embedding, ref) for ref in ref_embeddings
        )
        _record_semantic_success(api_key)
        if max_score >= settings.injection_semantic_threshold:
            result = InjectionDetectionResult(
                detected=True,
                level=2,
                method="semantic",
                pattern=None,
                score=max_score,
                normalized_input=normalized,
            )
        else:
            result = InjectionDetectionResult(
                detected=False,
                level=None,
                method=None,
                pattern=None,
                score=max_score,
                normalized_input=normalized,
            )
        # Deterministic for a given normalized input — safe to cache either
        # outcome. Only failures (timeout/error below) are left uncached so a
        # transient embedding hiccup doesn't pin a pass-through verdict.
        if cache_key is not None:
            await _semantic_cache_set(cache_key, result)
        return result
    except Exception as e:
        _record_semantic_failure(api_key)
        if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
            logger.warning("Async semantic injection check timeout: %s", e)
        else:
            logger.error("Async semantic injection check error: %s", e)

    return _passthrough_result(normalized)


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def _to_verdict(result: InjectionDetectionResult) -> Verdict:
    """Adapt the internal two-level result into the uniform guard contract."""
    if not result.detected:
        return Verdict.of(VerdictReason.OK, score=result.score or 0.0)
    if result.level == 1:
        return Verdict.of(
            VerdictReason.INJECTION_STRUCTURAL,
            score=result.score if result.score is not None else 1.0,
            evidence=result.pattern,
        )
    return Verdict.of(
        VerdictReason.INJECTION_SEMANTIC,
        score=result.score or 0.0,
        evidence="semantic_similarity",
    )


async def async_detect_injection(
    text: str,
    *,
    tenant_id: str,
    api_key: str,
    trace: TraceHandle | None = None,
) -> Verdict:
    """Two-level injection detection. Returns a :class:`Verdict`.

    Level 1 (structural) runs first, is CPU-bound and executes synchronously
    (~0 ms); if it triggers, level 2 is skipped. Level 2 (semantic) is gated
    by INJECTION_SEMANTIC_ENABLED and uses ``async_detect_injection_semantic``
    so the event loop is not blocked during the embedding HTTP call.
    """
    # Level 1: structural (~0 ms, CPU-bound — no await needed)
    _l1_start = perf_counter()
    result = detect_injection_structural(text)
    _l1_ms = round((perf_counter() - _l1_start) * 1000, 2)
    if trace is not None:
        _l1_span = trace.span(
            name="injection_l1",
            input={"question_preview": text[:80]},
        )
        _l1_span.end(
            output={"detected": result.detected, "pattern": result.pattern},
            metadata={"duration_ms": _l1_ms, "method": "structural"},
        )
        record_stage_ms(trace, "injection_guard_ms", _l1_ms)
    if result.detected:
        _log_detection(tenant_id, result)
        return _to_verdict(result)

    # Level 2: async semantic (~50-100 ms)
    if settings.injection_semantic_enabled:
        _l2_start = perf_counter()
        result = await async_detect_injection_semantic(
            text, result.normalized_input, api_key=api_key, tenant_id=tenant_id,
        )
        _l2_ms = round((perf_counter() - _l2_start) * 1000, 2)
        if trace is not None:
            _l2_span = trace.span(
                name="injection_l2",
                input={"question_preview": text[:80]},
            )
            _l2_span.end(
                output={"detected": result.detected, "score": result.score},
                metadata={"duration_ms": _l2_ms, "method": "semantic"},
            )
            record_stage_ms(trace, "injection_guard_ms", _l2_ms)
        if result.detected:
            _log_detection(tenant_id, result)

    return _to_verdict(result)


def _log_detection(tenant_id: str, result: InjectionDetectionResult) -> None:
    logger.warning(
        "Injection detected — tenant=%s level=%s method=%s",
        tenant_id,
        result.level,
        result.method,
    )
