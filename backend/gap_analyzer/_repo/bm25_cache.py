"""Thread-safe BM25 corpus LRU/TTL cache and BM25 scoring helper."""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer._math import _tokenize
from backend.gap_analyzer._repo.capabilities import _string_or_none
from backend.models import Document, Embedding

_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_SMOOTHING = 0.5
_BM25_MIN_MATCHED_QUERY_TERMS = 2
_BM25_CORPUS_CACHE_TTL_SECONDS = 300
_BM25_CORPUS_CACHE_MAX_ENTRIES = 128


@dataclass(frozen=True)
class _CachedBm25Document:
    exact_match_candidates: tuple[str, ...]
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class _CachedBm25Corpus:
    documents: tuple[_CachedBm25Document, ...]
    cached_at_monotonic: float


_BM25_CORPUS_CACHE: dict[tuple[UUID, tuple[str, ...]], _CachedBm25Corpus] = {}
_BM25_CORPUS_CACHE_LOCK = threading.RLock()


def _normalize_bm25_excluded_file_types(
    excluded_file_types: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(sorted(value.casefold() for value in excluded_file_types))


def _bm25_cache_key(
    tenant_id: UUID,
    excluded_file_types: tuple[str, ...],
) -> tuple[UUID, tuple[str, ...]]:
    return (tenant_id, _normalize_bm25_excluded_file_types(excluded_file_types))


def _evict_expired_bm25_cache_entries(now_monotonic: float) -> None:
    expired_keys = [
        key
        for key, corpus in _BM25_CORPUS_CACHE.items()
        if now_monotonic - corpus.cached_at_monotonic >= _BM25_CORPUS_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        _BM25_CORPUS_CACHE.pop(key, None)


def invalidate_bm25_cache_for_tenant(tenant_id: UUID | None) -> None:
    if tenant_id is None:
        return
    with _BM25_CORPUS_CACHE_LOCK:
        matching_keys = [key for key in _BM25_CORPUS_CACHE if key[0] == tenant_id]
        for key in matching_keys:
            _BM25_CORPUS_CACHE.pop(key, None)


def _bm25_streamed_score(
    *,
    doc_length: int,
    term_frequencies: dict[str, int],
    query_token_counts: Counter[str],
    idfs: dict[str, float],
    average_doc_length: float,
) -> float:
    if doc_length <= 0 or average_doc_length <= 0:
        return 0.0
    score = 0.0
    length_norm = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * (doc_length / average_doc_length))
    for token, query_count in query_token_counts.items():
        term_frequency = term_frequencies.get(token, 0)
        if term_frequency <= 0:
            continue
        idf = idfs.get(token, 0.0)
        if idf <= 0.0:
            continue
        score += query_count * idf * (
            (term_frequency * (_BM25_K1 + 1.0)) / (term_frequency + length_norm)
        )
    return score


class _Bm25CacheOps:
    def __init__(self, db: Session) -> None:
        self._db = db

    def load_or_cache(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> _CachedBm25Corpus:
        cache_key = _bm25_cache_key(tenant_id, excluded_file_types)
        now_monotonic = time.monotonic()
        with _BM25_CORPUS_CACHE_LOCK:
            cached = _BM25_CORPUS_CACHE.get(cache_key)
            if (
                cached is not None
                and now_monotonic - cached.cached_at_monotonic
                < _BM25_CORPUS_CACHE_TTL_SECONDS
            ):
                return cached
            _evict_expired_bm25_cache_entries(now_monotonic)

        excluded = set(cache_key[1])
        documents: list[_CachedBm25Document] = []
        rows = (
            self._db.query(
                Embedding.chunk_text,
                Embedding.metadata_json,
                Document.filename,
                Document.file_type,
            )
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.status == "ready")
            .filter(Embedding.chunk_text.isnot(None))
            .order_by(Document.id.asc(), Embedding.id.asc())
            .yield_per(500)
        )
        for chunk_text, metadata_json, filename, file_type in rows:
            file_type_value = str(getattr(file_type, "value", file_type)).casefold()
            if file_type_value in excluded:
                continue
            metadata = metadata_json if isinstance(metadata_json, dict) else {}
            exact_match_candidates = tuple(
                candidate.casefold()
                for candidate in (
                    _string_or_none(metadata.get("section_title")),
                    _string_or_none(metadata.get("page_title")),
                    _string_or_none(filename),
                )
                if candidate
            )
            tokens = tuple(_tokenize(chunk_text or ""))
            if not exact_match_candidates and not tokens:
                continue
            documents.append(
                _CachedBm25Document(
                    exact_match_candidates=exact_match_candidates,
                    tokens=tokens,
                )
            )

        cached = _CachedBm25Corpus(
            documents=tuple(documents),
            cached_at_monotonic=now_monotonic,
        )
        with _BM25_CORPUS_CACHE_LOCK:
            _BM25_CORPUS_CACHE[cache_key] = cached
            if len(_BM25_CORPUS_CACHE) > _BM25_CORPUS_CACHE_MAX_ENTRIES:
                oldest_key = min(
                    _BM25_CORPUS_CACHE,
                    key=lambda key: _BM25_CORPUS_CACHE[key].cached_at_monotonic,
                )
                _BM25_CORPUS_CACHE.pop(oldest_key, None)
        return cached
