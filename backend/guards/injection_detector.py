"""Injection detector v2 — language-agnostic, two-level detection.

Level 1: Structural patterns (sync, ~0 ms, no API calls).
Level 2: Semantic embedding similarity (sync or async with timeout, ~50-100 ms).

Any level triggering → immediate reject; subsequent levels are skipped.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from time import perf_counter

from backend.core.config import settings
from backend.observability import TraceHandle, record_stage_ms
from backend.search.service import (
    async_embed_queries,
    async_embed_query,
    cosine_similarity,
    embed_queries,
    embed_query,
)

logger = logging.getLogger(__name__)

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


def _get_reference_embeddings(api_key: str) -> list[list[float]]:
    """Lazy-init: compute seed embeddings once, cache in process memory."""
    global _reference_embeddings
    if _reference_embeddings is None:
        from backend.guards.injection_seeds import INJECTION_SEEDS

        _reference_embeddings = embed_queries(
            INJECTION_SEEDS,
            api_key=api_key,
            timeout=settings.injection_semantic_timeout_sec,
        )
    return _reference_embeddings


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


def detect_injection_semantic(
    text: str,
    normalized: str,
    *,
    api_key: str,
) -> InjectionDetectionResult:
    """Level 2: semantic similarity with injection seeds (sync).

    Uses the OpenAI HTTP client timeout (``INJECTION_SEMANTIC_TIMEOUT_SEC``)
    so requests are cancelled at the transport layer instead of leaving work
    running in a background thread after a futures timeout.
    On timeout or error → pass-through.
    """
    try:
        embedding = embed_query(
            text,
            api_key=api_key,
            timeout=settings.injection_semantic_timeout_sec,
        )
        ref_embeddings = _get_reference_embeddings(api_key)
        max_score = max(
            cosine_similarity(embedding, ref) for ref in ref_embeddings
        )
        if max_score >= settings.injection_semantic_threshold:
            return InjectionDetectionResult(
                detected=True,
                level=2,
                method="semantic",
                pattern=None,
                score=max_score,
                normalized_input=normalized,
            )
        return InjectionDetectionResult(
            detected=False,
            level=None,
            method=None,
            pattern=None,
            score=max_score,
            normalized_input=normalized,
        )
    except Exception as e:
        if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
            logger.warning("Semantic injection check timeout: %s", e)
        else:
            logger.error("Semantic injection check error: %s", e)

    return InjectionDetectionResult(
        detected=False,
        level=None,
        method=None,
        pattern=None,
        score=None,
        normalized_input=normalized,
    )


async def async_detect_injection_semantic(
    text: str,
    normalized: str,
    *,
    api_key: str,
) -> InjectionDetectionResult:
    """Level 2: semantic similarity with injection seeds (async).

    Async counterpart of :func:`detect_injection_semantic`. Uses
    ``async_embed_query`` so the event loop is not blocked during the
    OpenAI HTTP call. On timeout or error → pass-through (detected=False).
    """
    try:
        embedding = await async_embed_query(
            text,
            api_key=api_key,
            timeout=settings.injection_semantic_timeout_sec,
        )
        ref_embeddings = await _get_reference_embeddings_async(api_key)
        max_score = max(
            cosine_similarity(embedding, ref) for ref in ref_embeddings
        )
        if max_score >= settings.injection_semantic_threshold:
            return InjectionDetectionResult(
                detected=True,
                level=2,
                method="semantic",
                pattern=None,
                score=max_score,
                normalized_input=normalized,
            )
        return InjectionDetectionResult(
            detected=False,
            level=None,
            method=None,
            pattern=None,
            score=max_score,
            normalized_input=normalized,
        )
    except Exception as e:
        if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
            logger.warning("Async semantic injection check timeout: %s", e)
        else:
            logger.error("Async semantic injection check error: %s", e)

    return InjectionDetectionResult(
        detected=False,
        level=None,
        method=None,
        pattern=None,
        score=None,
        normalized_input=normalized,
    )


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def detect_injection(
    text: str,
    *,
    tenant_id: str,
    api_key: str,
    trace: TraceHandle | None = None,
) -> InjectionDetectionResult:
    """Two-level injection detection (sync).

    Level 1 (structural) runs first; if it triggers, level 2 is skipped.
    Level 2 (semantic) is gated by INJECTION_SEMANTIC_ENABLED.
    """
    # Level 1: structural (~0 ms)
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
        return result

    # Level 2: semantic (~50-100 ms)
    if settings.injection_semantic_enabled:
        _l2_start = perf_counter()
        result = detect_injection_semantic(
            text, result.normalized_input, api_key=api_key,
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
            return result
        return result

    return result


async def async_detect_injection(
    text: str,
    *,
    tenant_id: str,
    api_key: str,
    trace: TraceHandle | None = None,
) -> InjectionDetectionResult:
    """Async counterpart of :func:`detect_injection`.

    Level 1 (structural) is CPU-bound and runs synchronously (~0 ms).
    Level 2 (semantic) uses ``async_detect_injection_semantic`` so the
    event loop is not blocked during the embedding HTTP call.
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
        return result

    # Level 2: async semantic (~50-100 ms)
    if settings.injection_semantic_enabled:
        _l2_start = perf_counter()
        result = await async_detect_injection_semantic(
            text, result.normalized_input, api_key=api_key,
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
            return result
        return result

    return result


def _log_detection(tenant_id: str, result: InjectionDetectionResult) -> None:
    logger.warning(
        "Injection detected — tenant=%s level=%s method=%s",
        tenant_id,
        result.level,
        result.method,
    )


# Legacy alias kept for backward compatibility during migration.
def detect_prompt_injection(text: str) -> InjectionDetectionResult:
    """Deprecated — use detect_injection() for full two-level detection."""
    return detect_injection_structural(text)
