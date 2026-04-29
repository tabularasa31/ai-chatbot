"""Entity extraction service for the entity-overlap retrieval channel.

Wraps the HippoRAG-style NER prompts in ``backend.knowledge.prompts`` with an
OpenAI call, retry/timeout policy, JSON parsing, and a graceful fallback. The
two public entry points are:

- ``extract_entities_from_query``: short, latency-sensitive — used at chat
  request time alongside dense + BM25 retrieval. Wrapped in a wall-clock
  timeout so a slow NER call cannot stall the chat hot path.
- ``extract_entities_from_passage``: indexing-time — runs in a background
  worker over FAQ chunks once per ingest. No hot-path timeout; the OpenAI
  client's read timeout governs.

Failure policy: every failure mode (timeout, retry exhaustion, JSON garbage,
permanent OpenAI errors) is logged and returns ``[]``. The caller's hybrid
retrieval still has dense + BM25 channels, so an empty entity list degrades
to today's behavior — never raises into chat.

The ``response_format={"type": "json_object"}`` guarantee narrows JSON
shapes a lot, but we still defensively coerce: missing key → ``[]``;
non-list value → ``[]``; non-string items inside the list → dropped.
Whitespace is stripped and empty strings are filtered. Order is preserved.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry
from backend.knowledge.prompts import (
    build_ner_passage_messages,
    build_ner_query_messages,
)

logger = logging.getLogger(__name__)

# Public sentinels for callers that want to distinguish "no entities found"
# from "extractor failed" without relying on logs. Both currently collapse to
# an empty list — kept here so the contract is explicit.
EMPTY_ENTITIES: list[str] = []


def extract_entities_from_query(
    query: str,
    encrypted_api_key: str | None,
    *,
    tenant_id: str | None = None,
    bot_id: str | None = None,
) -> list[str]:
    """Extract named entities from a user query for the retrieval hot path.

    Wall-clock budget = ``settings.ner_query_timeout_seconds``. On timeout,
    OpenAI error, or unparseable JSON, returns ``[]`` — never raises.

    Args:
        query: User question. Empty / whitespace-only short-circuits to ``[]``
            without an LLM call.
        encrypted_api_key: Tenant's encrypted OpenAI key (from
            ``client.openai_api_key``). When ``None`` or empty, returns ``[]``.
        tenant_id: For retry telemetry attribution.
        bot_id: For retry telemetry attribution.
    """
    if not query or not query.strip():
        return list(EMPTY_ENTITIES)
    if not encrypted_api_key:
        return list(EMPTY_ENTITIES)

    timeout_seconds = settings.ner_query_timeout_seconds

    def _call() -> list[str]:
        return _run_ner(
            messages=build_ner_query_messages(query),
            encrypted_api_key=encrypted_api_key,
            operation="ner_query",
            tenant_id=tenant_id,
            bot_id=bot_id,
        )

    return _run_with_timeout(
        _call,
        timeout_seconds=timeout_seconds,
        operation="ner_query",
    )


def extract_entities_from_passage(
    passage: str,
    encrypted_api_key: str | None,
    *,
    tenant_id: str | None = None,
    bot_id: str | None = None,
) -> list[str]:
    """Extract named entities from an FAQ passage for indexing-time use.

    No wall-clock timeout: indexing runs in a background worker, and the
    OpenAI client's own read timeout already bounds the call. On any error
    or unparseable JSON, returns ``[]`` — the caller can decide whether to
    skip the chunk or write an empty entity list.

    Args:
        passage: FAQ chunk text. Empty / whitespace-only short-circuits to
            ``[]`` without an LLM call.
        encrypted_api_key: Tenant's encrypted OpenAI key.
        tenant_id: For retry telemetry attribution.
        bot_id: For retry telemetry attribution.
    """
    if not passage or not passage.strip():
        return list(EMPTY_ENTITIES)
    if not encrypted_api_key:
        return list(EMPTY_ENTITIES)

    try:
        return _run_ner(
            messages=build_ner_passage_messages(passage),
            encrypted_api_key=encrypted_api_key,
            operation="ner_passage",
            tenant_id=tenant_id,
            bot_id=bot_id,
        )
    except Exception:
        # _run_ner already converts known failure paths to [], so anything
        # leaking here is unexpected. Log and degrade.
        logger.exception("ner_passage_unexpected_failure")
        return list(EMPTY_ENTITIES)


def _run_ner(
    *,
    messages: list[dict[str, str]],
    encrypted_api_key: str,
    operation: str,
    tenant_id: str | None,
    bot_id: str | None,
) -> list[str]:
    """Single OpenAI NER call → parsed entity list. Returns [] on any error."""
    try:
        client = get_openai_client(encrypted_api_key)
    except Exception:
        # Permission / decryption / config failure — log and degrade.
        logger.warning("ner_client_init_failed", extra={"operation": operation})
        return list(EMPTY_ENTITIES)

    try:
        response = call_openai_with_retry(
            operation,
            lambda: client.chat.completions.create(
                model=settings.ner_model,
                messages=messages,
                temperature=0,
                max_completion_tokens=settings.ner_max_completion_tokens,
                response_format={"type": "json_object"},
            ),
            tenant_id=tenant_id,
            bot_id=bot_id,
            call_type="chat_completion",
        )
    except Exception:
        # Retry-exhausted, permanent OpenAI error, network — all degrade.
        logger.warning("ner_call_failed", extra={"operation": operation})
        return list(EMPTY_ENTITIES)

    return _parse_entities(response, operation=operation)


def _parse_entities(response: Any, *, operation: str) -> list[str]:
    """Extract ``named_entities`` from an OpenAI chat completion response."""
    try:
        raw = response.choices[0].message.content or "{}"
    except (AttributeError, IndexError, TypeError):
        logger.warning("ner_response_malformed", extra={"operation": operation})
        return list(EMPTY_ENTITIES)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "ner_json_decode_failed",
            extra={"operation": operation, "raw_prefix": raw[:120]},
        )
        return list(EMPTY_ENTITIES)

    if not isinstance(payload, dict):
        return list(EMPTY_ENTITIES)

    items = payload.get("named_entities")
    if not isinstance(items, list):
        return list(EMPTY_ENTITIES)

    seen: set[str] = set()
    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized:
            continue
        # Dedup case-sensitively — "Pro" and "pro" can be distinct entities
        # for retrieval (acronyms vs. common words). Preserve first occurrence.
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _run_with_timeout(
    fn: Any,
    *,
    timeout_seconds: float,
    operation: str,
) -> list[str]:
    """Run ``fn`` in a worker thread with a hard wall-clock timeout.

    On timeout, returns ``[]`` and lets the worker thread finish in the
    background — there is no safe way to cancel a blocking OpenAI HTTP call
    from outside the thread, so we let it drain. This mirrors the pattern
    used in ``backend/escalation/service.detect_human_request``.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        result = future.result(timeout=timeout_seconds)
    except TimeoutError:
        logger.warning(
            "ner_timeout",
            extra={"operation": operation, "timeout_seconds": timeout_seconds},
        )
        result = list(EMPTY_ENTITIES)
    except Exception:
        logger.warning("ner_unexpected_error", extra={"operation": operation})
        result = list(EMPTY_ENTITIES)
    finally:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)
    return result
