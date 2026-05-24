"""Heuristic detector for "do you want me to open a support ticket?" offers.

**Eval-only.** The production runtime uses a language-agnostic
machine-readable sentinel (``OFFER_MARKER`` in ``backend.chat.handlers.rag``)
that the LLM appends to its reply and the backend strips before persisting.
That signal is unavailable to ``backend.evals``: the eval driver consumes
the cleaned answer text from the chat API, so it has to fall back to
natural-language matching to score whether a turn offered a ticket.

This module keeps the heuristic for that single eval use case. Do NOT
reintroduce it on the live request path — it only covers RU and EN and would
silently miss ticket offers in other languages.

The patterns require both an action verb (open/forward/create) AND a
support-team noun (ticket/support) so that an unrelated mention of "ticket" in
an answer body doesn't trip the check.
"""

from __future__ import annotations

import re

_ESCALATION_OFFER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Russian — verb stems with permissive endings so we catch infinitives,
    # past-tense and other conjugations ("открыл", "открыть", "отправил",
    # "отправить", "создам", "создаст" …) without enumerating each form.
    # re.DOTALL so the .{0,40} bridge tolerates a newline between the verb
    # and the noun ("открыть\nтикет"), which LLMs do emit on multi-line offers.
    re.compile(
        r"(перешл[а-яё]*|откр[ыо][а-яё]*|созда[а-яё]*|отправ[а-яё]*)"
        r".{0,40}(тикет|поддержк|обращени)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(перед[а-яё]*|пересл[а-яё]*).{0,40}(поддержк|команд)",
        re.IGNORECASE | re.DOTALL,
    ),
    # English
    re.compile(
        r"(open|file|create|raise|forward).{0,40}(ticket|support|case)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(would you like|want me to|shall i).{0,80}(support|ticket|team)",
        re.IGNORECASE | re.DOTALL,
    ),
)


def looks_like_escalation_offer(text: str) -> bool:
    """Return True if ``text`` ends-with / contains a ticket-offer pattern."""
    if not text:
        return False
    return any(p.search(text) for p in _ESCALATION_OFFER_PATTERNS)
