"""Heuristic detector for "do you want me to open a support ticket?" offers.

The RAG system prompt instructs the LLM to offer a support ticket when it
genuinely can't answer from the documentation, and to wait for the user's
confirmation. The backend arms ``chat.escalation_pre_confirm_pending`` so that
the next user reply ("yes" / "да") is routed to the escalation state machine.

The arming happens through ``decide()`` in the policy layer. When the LLM
disagrees with the classifier — typing "I don't have this in the docs, want a
ticket?" on a turn the classifier scored as a confident answer — the flag is
never set, and the user's "да" falls through to ``SmallTalkHandler``. This
detector catches that mismatch by inspecting the rendered answer text.

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
    re.compile(
        r"(перешл[а-яё]*|откр[ыо][а-яё]*|созда[а-яё]*|отправ[а-яё]*)"
        r".{0,40}(тикет|поддержк|обращени)",
        re.IGNORECASE,
    ),
    re.compile(r"(перед[а-яё]*|пересл[а-яё]*).{0,40}(поддержк|команд)", re.IGNORECASE),
    # English
    re.compile(r"(open|file|create|raise|forward).{0,40}(ticket|support|case)", re.IGNORECASE),
    re.compile(r"(would you like|want me to|shall i).{0,80}(support|ticket|team)", re.IGNORECASE),
)


def looks_like_escalation_offer(text: str) -> bool:
    """Return True if ``text`` ends-with / contains a ticket-offer pattern."""
    if not text:
        return False
    return any(p.search(text) for p in _ESCALATION_OFFER_PATTERNS)
