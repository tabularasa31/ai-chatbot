"""Unit tests for the loop-detection signal and clarify-anchor helper in
the RAG handler.

Both helpers operate on a ``Chat`` instance with its ``messages`` already
loaded. We pass lightweight stand-in objects (a dataclass shim) rather
than spin up a real ORM session — the helpers only read ``role``,
``source_documents``, ``created_at``, and ``id``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from backend.chat.handlers.rag import (
    _clarify_anchor_turn_id,
    _compute_loop_signal,
)
from backend.models import MessageRole


@dataclass
class _MsgStub:
    role: MessageRole
    source_documents: list[Any] | None
    created_at: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class _ChatStub:
    messages: list[_MsgStub]


def _at(seconds: int) -> datetime:
    return datetime(2026, 5, 17, 12, 0, 0) + timedelta(seconds=seconds)


def _user(t: int) -> _MsgStub:
    return _MsgStub(role=MessageRole.user, source_documents=None, created_at=_at(t))


def _assistant(t: int, docs: list[str]) -> _MsgStub:
    return _MsgStub(
        role=MessageRole.assistant,
        source_documents=[uuid.UUID(d) for d in docs],
        created_at=_at(t),
    )


# Stable doc UUIDs for readability — order matters for overlap intuition.
DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"
DOC_C = "33333333-3333-3333-3333-333333333333"
DOC_D = "44444444-4444-4444-4444-444444444444"


# ---------------------------------------------------------------------------
# _compute_loop_signal
# ---------------------------------------------------------------------------


def test_loop_signal_returns_false_for_none_chat() -> None:
    assert _compute_loop_signal(None, window=3, min_overlap=0.5) == (False, None, 0)


def test_loop_signal_returns_false_for_window_lt_two() -> None:
    chat = _ChatStub(
        messages=[
            _user(1), _assistant(2, [DOC_A]),
            _user(3), _assistant(4, [DOC_A]),
        ]
    )
    assert _compute_loop_signal(chat, window=1, min_overlap=0.5) == (False, None, 0)


def test_loop_signal_returns_false_when_fewer_than_window_turns() -> None:
    chat = _ChatStub(
        messages=[
            _user(1), _assistant(2, [DOC_A, DOC_B]),
            _user(3), _assistant(4, [DOC_A, DOC_B]),
        ]
    )
    detected, overlap, window = _compute_loop_signal(chat, window=3, min_overlap=0.5)
    assert detected is False
    assert window == 2  # effective assistant-turn count


def test_loop_signal_detects_full_overlap_across_window() -> None:
    """Three consecutive assistant turns drawing on the exact same docs."""
    chat = _ChatStub(
        messages=[
            _user(1), _assistant(2, [DOC_A, DOC_B]),
            _user(3), _assistant(4, [DOC_A, DOC_B]),
            _user(5), _assistant(6, [DOC_A, DOC_B]),
        ]
    )
    detected, overlap, window = _compute_loop_signal(chat, window=3, min_overlap=0.5)
    assert detected is True
    assert overlap == 1.0
    assert window == 3


def test_loop_signal_no_loop_when_docs_differ() -> None:
    """Three assistant turns on different docs — user is asking varied questions."""
    chat = _ChatStub(
        messages=[
            _user(1), _assistant(2, [DOC_A]),
            _user(3), _assistant(4, [DOC_B]),
            _user(5), _assistant(6, [DOC_C]),
        ]
    )
    detected, overlap, window = _compute_loop_signal(chat, window=3, min_overlap=0.5)
    assert detected is False
    assert overlap == 0.0


def test_loop_signal_uses_max_pairwise_overlap_not_mean() -> None:
    """A → B → A pattern: A/A overlap is 1.0 even though A/B is 0.0.
    Max-overlap heuristic catches the orbit; mean would miss it."""
    chat = _ChatStub(
        messages=[
            _user(1), _assistant(2, [DOC_A]),
            _user(3), _assistant(4, [DOC_B]),
            _user(5), _assistant(6, [DOC_A]),
        ]
    )
    detected, overlap, window = _compute_loop_signal(chat, window=3, min_overlap=0.5)
    assert detected is True
    assert overlap == 1.0


def test_loop_signal_resets_when_assistant_turn_has_no_docs() -> None:
    """A greeting / small-talk / escalation handoff in the middle resets the
    loop chain — those turns aren't grounded in KB and shouldn't pollute
    the heuristic."""
    chat = _ChatStub(
        messages=[
            _user(1), _assistant(2, [DOC_A, DOC_B]),
            _user(3), _assistant(4, [DOC_A, DOC_B]),
            _user(5), _assistant(6, []),                    # reset
            _user(7), _assistant(8, [DOC_A, DOC_B]),
        ]
    )
    detected, overlap, window = _compute_loop_signal(chat, window=3, min_overlap=0.5)
    assert detected is False
    assert window == 1  # only the post-reset assistant turn is in the chain


@pytest.mark.parametrize(
    "min_overlap,expected",
    [(0.5, True), (0.75, False)],
)
def test_loop_signal_respects_threshold(min_overlap: float, expected: bool) -> None:
    """Jaccard of {A,B} vs {A,C} is 1/3 ≈ 0.33; vs {A,B} is 1.0.
    Max across pairs = 1.0, so 0.5 threshold trips, but with all three at
    Jaccard ~0.33 (no full overlap) the 0.75 threshold does not."""
    if expected:
        chat = _ChatStub(
            messages=[
                _user(1), _assistant(2, [DOC_A, DOC_B]),
                _user(3), _assistant(4, [DOC_A, DOC_C]),
                _user(5), _assistant(6, [DOC_A, DOC_B]),
            ]
        )
    else:
        chat = _ChatStub(
            messages=[
                _user(1), _assistant(2, [DOC_A, DOC_B]),
                _user(3), _assistant(4, [DOC_A, DOC_C]),
                _user(5), _assistant(6, [DOC_A, DOC_D]),
            ]
        )
    detected, _, _ = _compute_loop_signal(chat, window=3, min_overlap=min_overlap)
    assert detected is expected


# ---------------------------------------------------------------------------
# _clarify_anchor_turn_id
# ---------------------------------------------------------------------------


def test_clarify_anchor_returns_none_for_empty_chat() -> None:
    assert _clarify_anchor_turn_id(None) is None
    assert _clarify_anchor_turn_id(_ChatStub(messages=[])) is None


def test_clarify_anchor_returns_id_of_last_user_message() -> None:
    last_user = _user(5)
    chat = _ChatStub(
        messages=[
            _user(1),
            _assistant(2, [DOC_A]),
            _user(3),
            _assistant(4, [DOC_B]),
            last_user,
        ]
    )
    assert _clarify_anchor_turn_id(chat) == str(last_user.id)


def test_clarify_anchor_skips_assistant_messages() -> None:
    """When the most recent message is assistant (rare — current turn isn't
    persisted yet but historical edge cases exist), pick the latest user."""
    last_user = _user(3)
    chat = _ChatStub(
        messages=[
            _user(1),
            _assistant(2, [DOC_A]),
            last_user,
            _assistant(4, [DOC_B]),
        ]
    )
    assert _clarify_anchor_turn_id(chat) == str(last_user.id)
