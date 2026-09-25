"""Query expansion, script detection, and LLM-based query rewrite helpers."""

from __future__ import annotations

import re
from typing import Any

from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client
from backend.core.openai_retry import async_call_openai_with_retry
from backend.core.scripts import NO_SCRIPT_BUCKET, detect_script_bucket
from backend.utils.text import word_tokens

# HTTP timeout for the query-rewrite LLM call. The effective latency cap is
# settings.openai_user_retry_budget_seconds (default 1.5s) — this value only
# matters if the retry budget is raised above it.
QUERY_REWRITE_HTTP_TIMEOUT_SECONDS = 3.0

_SEMANTIC_REWRITE_MAX_TOKENS = 40  # enough for a short keyword phrase

_SEMANTIC_REWRITE_PROMPT_PREFIX = (
    "A customer asked a product support chatbot:\n\""
)
_SEMANTIC_REWRITE_PROMPT_SUFFIX = (
    "\"\n\n"
    "Write a short technical search query (5-10 words) using product feature "
    "terminology and technical concepts that would retrieve the relevant "
    "documentation. Focus on the FEATURE or SETTING being asked about, not "
    "the user's symptom. Reply with ONLY the search query, nothing else."
)

# History-aware variant: given the recent dialog, the model itself decides
# whether the current message continues the conversation (and needs the prior
# topic folded into the query) or stands alone (and the history must be
# ignored). This replaces surface heuristics — word counts and affirmation
# dictionaries can't separate "yes, how do I check?" from "how to set up the
# widget": both are short, but only the first needs the previous turn.
_SEMANTIC_REWRITE_DIALOG_PREFIX = (
    "Recent conversation between a customer and a product support chatbot:\n"
)
_SEMANTIC_REWRITE_DIALOG_BRIDGE = "\n\nThe customer now says:\n\""
_SEMANTIC_REWRITE_DIALOG_SUFFIX = (
    "\"\n\n"
    "Write a short standalone search query (5-10 words) using product feature "
    "terminology and technical concepts that would retrieve the relevant "
    "documentation. If the customer's message reacts to the assistant's "
    "previous turn (an affirmation like \"yes\", a short continuation, a "
    "reference like \"and for teams?\"), resolve it against the conversation "
    "so the query names the actual topic being discussed. If the message is "
    "self-contained, ignore the conversation entirely. Focus on the FEATURE "
    "or SETTING being asked about, not the user's symptom. Write the query "
    "in the customer's language. Reply with ONLY the search query, nothing "
    "else."
)


def _normalize_query_variants(values: list[str]) -> list[str]:
    """Normalize and dedupe query variants while preserving first-seen order."""
    variants: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        variants.append(normalized)
    return variants


def detect_query_script_bucket(text: str) -> str:
    """Detect the writing system of the query text."""
    return detect_script_bucket(text)


def expand_query(query: str) -> list[str]:
    """Generate lightweight query variants without changing user intent."""
    variants: list[str] = []

    def _push(value: str) -> None:
        variants[:] = _normalize_query_variants([*variants, value])

    _push(query)

    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    _push(cleaned)

    tokens = word_tokens(query)
    if tokens:
        unique_tokens = list(dict.fromkeys(tokens))
        _push(" ".join(unique_tokens))

    return variants or [query]


def _build_semantic_rewrite_prompt(query: str, dialog_context: str | None) -> str:
    # Concatenation instead of .format() so curly braces in user input
    # (e.g. "{name}", "{0}") don't raise KeyError / IndexError.
    if dialog_context:
        return (
            _SEMANTIC_REWRITE_DIALOG_PREFIX
            + dialog_context
            + _SEMANTIC_REWRITE_DIALOG_BRIDGE
            + query
            + _SEMANTIC_REWRITE_DIALOG_SUFFIX
        )
    return _SEMANTIC_REWRITE_PROMPT_PREFIX + query + _SEMANTIC_REWRITE_PROMPT_SUFFIX


async def _async_rewrite_query_for_retrieval(
    query: str,
    *,
    api_key: str,
    langfuse_observation: Any | None = None,
) -> str | None:
    """Rewrite the user question as doc-style English keywords for retrieval.

    Bridges the framing gap between user problem-descriptions and
    feature-name headings in docs. Returns ``None`` on any failure.
    """
    try:
        client = get_async_openai_client(api_key, timeout=QUERY_REWRITE_HTTP_TIMEOUT_SECONDS)
        response = await async_call_openai_with_retry(
            "query_rewrite_for_retrieval",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a search query optimizer for a product knowledge base.\n"
                            "Rewrite the user's question as 3-5 English keywords or a short English noun phrase "
                            "that would appear as a topic or heading in product documentation.\n"
                            "Always output in English regardless of the input language.\n"
                            "Output only the rewritten query, nothing else."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_completion_tokens=60,
            ),
            langfuse_observation=langfuse_observation,
        )
        rewritten = (response.choices[0].message.content or "").strip()
        return rewritten if rewritten else None
    except Exception:
        return None


async def _run_semantic_rewrite(
    prompt: str,
    *,
    api_key: str,
    timeout: float,
    bot_id: str | None,
    langfuse_observation: Any | None,
) -> str | None:
    """Shared single-message rewrite call used by both semantic-rewrite variants."""
    try:
        client = get_async_openai_client(api_key, timeout=timeout)
        response = await async_call_openai_with_retry(
            "semantic_query_rewrite",
            lambda: client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_completion_tokens=_SEMANTIC_REWRITE_MAX_TOKENS,
            ),
            bot_id=bot_id,
            langfuse_observation=langfuse_observation,
        )
        rewrite = (response.choices[0].message.content or "").strip()
        if rewrite and "\n" not in rewrite and len(rewrite) <= 200:
            return rewrite
    except Exception:
        pass
    return None


async def async_semantic_query_rewrite(
    query: str,
    *,
    api_key: str,
    timeout: float = 2.0,
    bot_id: str | None = None,
    langfuse_observation: Any | None = None,
    dialog_context: str | None = None,
) -> str | None:
    """LLM-based semantic rewrite: user symptom → feature/product terminology.

    Bridges the semantic gap between how users describe problems ("bot replies
    only in Russian") and how documentation describes features ("language
    detection multilingual settings"). Language-agnostic: the LLM stays in
    the same language as the query so the multilingual embedding model can
    match chunks regardless of what language the docs are in.

    When ``dialog_context`` is provided (rendered by
    ``backend.chat.followup.build_dialog_context``), the model additionally
    resolves conversational continuations ("yes, how do I check?") into a
    standalone query against the prior turns, and ignores the history for
    self-contained questions.

    Returns None on any failure so the caller degrades gracefully to lexical
    variants only. Used for vector retrieval only, not BM25.
    """
    if not query or not api_key:
        return None
    prompt = _build_semantic_rewrite_prompt(query, dialog_context)
    return await _run_semantic_rewrite(
        prompt,
        api_key=api_key,
        timeout=timeout,
        bot_id=bot_id,
        langfuse_observation=langfuse_observation,
    )


async def async_semantic_query_rewrite_for_kb(
    query: str,
    *,
    kb_script: str,
    api_key: str,
    timeout: float = 2.0,
    bot_id: str | None = None,
    langfuse_observation: Any | None = None,
) -> str | None:
    """Rewrite the user query in the language of the knowledge base.

    Used when the query language and KB language differ (e.g. English query
    against a Cyrillic corpus).  Generates a KB-language variant that is added
    to the vector query pool so embeddings match same-language chunks more
    reliably.  Fails silently — returns None on any error.
    """
    if kb_script == NO_SCRIPT_BUCKET or not kb_script or not query or not api_key:
        return None
    prompt = (
        _SEMANTIC_REWRITE_PROMPT_PREFIX
        + query
        + "\"\n\n"
        "Write a short technical search query (5-10 words) using product feature "
        "terminology and technical concepts that would retrieve the relevant "
        "documentation. Focus on the FEATURE or SETTING being asked about, not "
        "the user's symptom. Write the query in the language of the knowledge "
        f"base, which uses the {kb_script} script — output ONLY in that script, "
        "regardless of the input language. Reply with ONLY the search query, "
        "nothing else."
    )
    return await _run_semantic_rewrite(
        prompt,
        api_key=api_key,
        timeout=timeout,
        bot_id=bot_id,
        langfuse_observation=langfuse_observation,
    )
