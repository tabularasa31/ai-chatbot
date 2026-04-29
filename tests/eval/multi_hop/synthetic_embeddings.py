"""Deterministic synthetic embeddings for the multi-hop eval harness.

Real OpenAI embeddings are non-deterministic across model upgrades and cost
money to recompute every time the eval runs in CI. We need a deterministic
local embedder so:

- Two runs produce the same numbers (the harness measures *retriever*
  improvements, not embedding-model improvements).
- The eval can run without an OpenAI key.

Approach: hashed bag-of-words on top of token-seeded random unit vectors.
Each unique token maps to a fixed pseudo-random unit vector in R^1536. A
text's embedding is the L2-normalized sum of its token vectors. This makes
texts that share many tokens have higher cosine similarity — a faithful
caricature of what a real embedding model does for short FAQ chunks.

Properties:
- Fully deterministic: ``embed(text)`` only depends on the tokens in
  ``text`` and the global seed.
- Same dimension (1536) and unit-norm as ``text-embedding-3-small`` so it
  can populate ``Embedding.vector`` directly.
- Lexical-similar texts cluster; lexical-disjoint texts are near-orthogonal.
- Synonyms / paraphrases that share NO tokens look unrelated — exactly
  the failure mode entity-aware retrieval is supposed to *not* rely on
  fixing. The eval is about whether the retriever surfaces the right
  chunks given that signal mix; we are not pretending these are
  state-of-the-art embeddings.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct

EMBEDDING_DIM = 1536


def _token_vector(token: str) -> list[float]:
    """Deterministic unit-norm vector for a single lowercase token.

    Uses SHA-256 of the token, expanded into 1536 floats by repeated hashing.
    Each 4-byte chunk is unpacked as a signed int32 and divided by 2^31 to
    fall into [-1, 1). The full vector is then L2-normalized.
    """
    floats: list[float] = []
    counter = 0
    while len(floats) < EMBEDDING_DIM:
        block = hashlib.sha256(f"{token}|{counter}".encode("utf-8")).digest()
        # 32 bytes → 8 int32s → 8 floats per round; need 1536/8 = 192 rounds.
        for i in range(0, len(block), 4):
            (n,) = struct.unpack(">i", block[i : i + 4])
            floats.append(n / 2**31)
            if len(floats) >= EMBEDDING_DIM:
                break
        counter += 1
    norm = math.sqrt(sum(x * x for x in floats))
    if norm == 0.0:
        return floats
    return [x / norm for x in floats]


_TOKEN_CACHE: dict[str, list[float]] = {}


def _cached_token_vector(token: str) -> list[float]:
    cached = _TOKEN_CACHE.get(token)
    if cached is not None:
        return cached
    vec = _token_vector(token)
    _TOKEN_CACHE[token] = vec
    return vec


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens — same shape as the BM25 prefilter.

    We use ``[a-z0-9]+`` rather than ``\\w+`` because BM25 matches on lowercased
    Latin tokens too, and we want the eval's signal mix to reflect that.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def embed_text(text: str) -> list[float]:
    """L2-normalized hashed bag-of-words embedding for ``text``."""
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * EMBEDDING_DIM
    acc = [0.0] * EMBEDDING_DIM
    for tok in tokens:
        vec = _cached_token_vector(tok)
        for i in range(EMBEDDING_DIM):
            acc[i] += vec[i]
    norm = math.sqrt(sum(x * x for x in acc))
    if norm == 0.0:
        return acc
    return [x / norm for x in acc]


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]
