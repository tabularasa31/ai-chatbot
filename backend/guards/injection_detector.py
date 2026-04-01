"""Injection detector v2 — language-agnostic, two-level detection.

Level 1: Structural patterns (sync, ~0 ms, no API calls).
Level 2: Semantic embedding similarity (sync with timeout, ~50-100 ms).

Any level triggering → immediate reject; subsequent levels are skipped.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from time import perf_counter

from backend.core.config import settings
from backend.search.service import cosine_similarity, embed_queries, embed_query

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
    # Pseudo-system blocks (brackets, XML, markdown, fences)
    r"\[\s*(system|admin|root|operator|developer|instruction|prompt)\s*\]",
    r"<\s*(system|admin|prompt|instruction|override)\s*[/>]",
    r"#{1,6}\s*(system|instruction|prompt|override|admin)",
    r"---+\s*(system|reset|new.?prompt|override)\s*---+",
    r"```\s*(system|prompt|instruction|admin)",
    # Context reset phrases (language-independent ASCII terms)
    r"\bnew\s+conversation\b",
    r"\breset\s+(context|history|instructions?)\b",
    r"\bclear\s+(context|history|instructions?)\b",
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


def _get_reference_embeddings(api_key: str) -> list[list[float]]:
    """Lazy-init: compute seed embeddings once, cache in process memory."""
    global _reference_embeddings
    if _reference_embeddings is None:
        from backend.guards.injection_seeds import INJECTION_SEEDS

        _reference_embeddings = embed_queries(INJECTION_SEEDS, api_key=api_key)
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
    """Level 2: semantic similarity with injection seeds.

    Uses ThreadPoolExecutor for timeout control (same pattern as
    relevance_checker).  On timeout or error → pass-through.
    """
    def _run() -> InjectionDetectionResult:
        embedding = embed_query(text, api_key=api_key)
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

    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(_run)
    try:
        return future.result(timeout=settings.injection_semantic_timeout_sec)
    except FuturesTimeoutError:
        logger.warning("Semantic injection check timeout")
    except Exception as e:
        logger.error("Semantic injection check error: %s", e)
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)

    return InjectionDetectionResult(
        detected=False,
        level=None,
        method=None,
        pattern=None,
        score=None,
        normalized_input=normalized,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def detect_injection(
    text: str,
    *,
    tenant_id: str,
    api_key: str,
) -> InjectionDetectionResult:
    """Two-level injection detection.

    Level 1 (structural) runs first; if it triggers, level 2 is skipped.
    Level 2 (semantic) is gated by INJECTION_SEMANTIC_ENABLED.
    """
    # Level 1: structural (~0 ms)
    result = detect_injection_structural(text)
    if result.detected:
        _log_detection(tenant_id, result)
        return result

    # Level 2: semantic (~50-100 ms)
    if settings.injection_semantic_enabled:
        result = detect_injection_semantic(
            text, result.normalized_input, api_key=api_key,
        )
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
