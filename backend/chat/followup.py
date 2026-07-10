"""Render recent dialog turns for per-turn LLM helpers.

The chat loop runs retrieval and per-turn classifiers on the latest user
message. That is fine for self-contained questions but breaks for
continuations: when the bot ends its previous turn with "Want me to help
with X?" and the user answers "yes, how?", the reply carries no retrievable
terms on its own.

``build_dialog_context`` renders the last few exchanges as a plain text
block. Two consumers feed it to LLM calls that resolve such continuations
semantically instead of guessing from surface features (word count,
affirmation dictionaries):

- the relevance guard (``backend.guards.relevance_checker``), so anaphoric
  replies ("what about X?") are judged against the conversation;
- the semantic query rewrite (``backend.search.service``), which turns a
  continuation into a standalone retrieval query — or ignores the history
  when the current message is self-contained.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from backend.models import Message, MessageRole

_MIN_DATETIME = datetime.min

_DIALOG_CONTEXT_TURNS = 2
_DIALOG_CONTEXT_CHAR_CAP = 240


def build_dialog_context(
    messages: Iterable[Message],
    *,
    max_turns: int = _DIALOG_CONTEXT_TURNS,
    char_cap: int = _DIALOG_CONTEXT_CHAR_CAP,
) -> str | None:
    """Render the last ``max_turns`` user/assistant exchanges as a plain block.

    Each message is truncated to ``char_cap`` chars so long cited answers
    don't blow up downstream prompts. User messages keep their head (the
    topic is stated up front); assistant messages keep their tail — the
    follow-up question the user may be reacting to almost always sits at the
    end of the bot's reply, and a head cut would drop exactly the part that
    lets consumers resolve a short "yes, how?" continuation. Returns None
    when there is no prior dialog to render.
    """
    ordered = sorted(
        messages,
        key=lambda m: (m.created_at or _MIN_DATETIME, m.id or 0),
    )
    recent: list[str] = []
    turns_seen = 0
    for m in reversed(ordered):
        content = (m.content or "").strip()
        if not content:
            continue
        if m.role == MessageRole.user:
            recent.append(f"User: {content[:char_cap]}")
            turns_seen += 1
            if turns_seen >= max_turns:
                break
        else:
            recent.append(f"Assistant: {content[-char_cap:]}")
    if not recent:
        return None
    return "\n".join(reversed(recent))
