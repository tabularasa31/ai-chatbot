"""Detect short user replies that continue the bot's previous follow-up question.

The chat loop generates standalone replies for each user turn — retrieval and
prompt assembly run on the latest user message in isolation. That is fine for
self-contained questions but breaks when the bot ends its previous turn with
"Want me to help with X?" and the user answers with a one-word affirmation.
Without context that reply matches no documents and the LLM cannot tell what
the user is reacting to.

This module provides two helpers used to bridge that gap:

- ``looks_like_short_followup``: cheap heuristic that flags the current user
  text as a likely continuation (very short and/or matching a multilingual
  affirmation set). Used to gate the more expensive contextual rewrite.
- ``build_contextual_retrieval_query``: combines the last assistant message
  (its tail, where the follow-up question lives) with the current user reply
  into a single string that gives BM25 / vector retrieval real terms to match.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from backend.models import Message, MessageRole

_MIN_DATETIME = datetime.min

# Multilingual affirmations / continuation cues. Lowercased, casefold-compared.
# Keep this list short and obvious — anything ambiguous should fall through to
# the word-count check instead of being hardcoded.
_AFFIRMATIONS: frozenset[str] = frozenset(
    {
        # Russian
        "да", "ага", "угу", "конечно", "давай", "давайте", "хочу", "хорошо",
        "ок", "окей", "продолжай", "продолжайте", "расскажи", "расскажите",
        "покажи", "покажите", "пожалуйста", "ладно",
        # English
        "yes", "yep", "yeah", "sure", "ok", "okay", "please", "go on",
        "continue", "tell me", "show me", "go ahead", "do it",
        # Spanish / French / German / Italian / Portuguese (compact set)
        "si", "sí", "claro", "vale",
        "oui", "d'accord", "bien sûr",
        "ja", "klar",
        "sì", "certo",
        "sim",
    }
)

_SHORT_WORD_LIMIT = 2
_TAIL_SENTENCE_CHARS = 400
_NON_WORD_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _NON_WORD_RE.sub(" ", text or "").strip().casefold()


def looks_like_short_followup(text: str) -> bool:
    """Return True if ``text`` looks like a short continuation of a prior turn.

    Two signals: word count ≤ ``_SHORT_WORD_LIMIT`` (covers things like "ну да"
    or "yes please") OR exact match against an affirmation phrase. Punctuation
    and whitespace are ignored. Empty / whitespace-only input returns False —
    callers handle empty messages elsewhere.
    """
    normalized = _normalize(text)
    if not normalized:
        return False
    stripped = re.sub(r"[^\w\s'’-]", " ", normalized, flags=re.UNICODE).strip()
    if not stripped:
        return False
    if stripped in _AFFIRMATIONS:
        return True
    return len(stripped.split()) <= _SHORT_WORD_LIMIT


def _last_assistant_message(messages: Iterable[Message]) -> Message | None:
    # Chat.messages has no DB-level ``order_by``, so iteration order on a
    # freshly loaded relationship is not guaranteed to match send order.
    # Sort by ``created_at`` (with ``id`` as a tiebreaker for messages
    # persisted in the same millisecond) before picking the latest assistant
    # turn — otherwise a short follow-up could attach to a stale assistant
    # message and steer retrieval to outdated context.
    ordered = sorted(
        messages,
        key=lambda m: (m.created_at or _MIN_DATETIME, m.id or 0),
    )
    last: Message | None = None
    for m in ordered:
        if m.role == MessageRole.assistant and (m.content or "").strip():
            last = m
    return last


def _tail_of(text: str, max_chars: int = _TAIL_SENTENCE_CHARS) -> str:
    """Take the trailing slice of an assistant message — the follow-up question
    is almost always at the end. Cap to ``max_chars`` so we don't drag long
    cited answers into the retrieval query.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:].lstrip()


def build_contextual_retrieval_query(
    messages: Iterable[Message],
    current_text: str,
) -> str | None:
    """Build a stand-alone retrieval query from the dialog tail.

    Concatenates the tail of the last assistant message with the current user
    reply. Returns None when there is no usable assistant context — callers
    should fall back to ``current_text`` as-is.
    """
    last_assistant = _last_assistant_message(messages)
    if last_assistant is None:
        return None
    tail = _tail_of(last_assistant.content or "")
    if not tail:
        return None
    user_part = (current_text or "").strip()
    if not user_part:
        return tail
    return f"{tail}\n{user_part}"
