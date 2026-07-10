"""Unit tests for the loop-detection signal and clarify-anchor helper in
the RAG handler.

Both helpers operate on a ``Chat`` instance with its ``messages`` already
loaded. We pass lightweight stand-in objects (a dataclass shim) rather
than spin up a real ORM session — the helpers only read ``role``,
``source_documents``, ``content``, ``created_at``, and ``id``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from backend.chat.handlers.rag import (
    LoopSignal,
    _clarify_anchor_turn_id,
    _compute_loop_signal,
)
from backend.models import MessageRole


@dataclass
class _MsgStub:
    role: MessageRole
    source_documents: list[Any] | None
    created_at: datetime
    content: str = ""
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class _ChatStub:
    messages: list[_MsgStub]


def _at(seconds: int) -> datetime:
    return datetime(2026, 5, 17, 12, 0, 0) + timedelta(seconds=seconds)


def _user(t: int, content: str = "") -> _MsgStub:
    return _MsgStub(
        role=MessageRole.user,
        source_documents=None,
        created_at=_at(t),
        content=content,
    )


def _assistant(t: int, docs: list[str]) -> _MsgStub:
    return _MsgStub(
        role=MessageRole.assistant,
        source_documents=[uuid.UUID(d) for d in docs],
        created_at=_at(t),
        content="answer",
    )


# Stable doc UUIDs for readability — order matters for overlap intuition.
DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"
DOC_C = "33333333-3333-3333-3333-333333333333"
DOC_D = "44444444-4444-4444-4444-444444444444"

REPEATED_QUESTION = "does turboflare provide web hosting"


def _signal(
    chat: _ChatStub | None,
    *,
    current_question: str | None = REPEATED_QUESTION,
    window: int = 3,
    min_overlap: float = 0.5,
    min_question_similarity: float = 0.6,
) -> LoopSignal:
    return _compute_loop_signal(
        chat,
        current_question=current_question,
        window=window,
        min_overlap=min_overlap,
        min_question_similarity=min_question_similarity,
    )


# ---------------------------------------------------------------------------
# _compute_loop_signal — guard clauses
# ---------------------------------------------------------------------------


def test_loop_signal_returns_no_loop_for_none_chat() -> None:
    assert _signal(None) == LoopSignal()


def test_loop_signal_returns_no_loop_for_window_lt_two() -> None:
    chat = _ChatStub(
        messages=[
            _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A]),
            _user(3, REPEATED_QUESTION), _assistant(4, [DOC_A]),
        ]
    )
    assert _signal(chat, window=1) == LoopSignal()


def test_loop_signal_returns_no_loop_when_fewer_than_window_turns() -> None:
    chat = _ChatStub(
        messages=[
            _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A, DOC_B]),
            _user(3, REPEATED_QUESTION), _assistant(4, [DOC_A, DOC_B]),
        ]
    )
    signal = _signal(chat)
    assert signal.detected is False
    assert signal.window_size == 2  # effective assistant-turn count


# ---------------------------------------------------------------------------
# _compute_loop_signal — docs + questions must BOTH repeat
# ---------------------------------------------------------------------------


def test_loop_signal_detects_real_loop_same_question_same_docs() -> None:
    """The user asks the same question three times and every answer drew on
    the same docs — a genuine loop, re-answering won't help."""
    chat = _ChatStub(
        messages=[
            _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A, DOC_B]),
            _user(3, REPEATED_QUESTION), _assistant(4, [DOC_A, DOC_B]),
            _user(5, REPEATED_QUESTION), _assistant(6, [DOC_A, DOC_B]),
        ]
    )
    signal = _signal(chat, current_question=REPEATED_QUESTION)
    assert signal.detected is True
    assert signal.docs_repeat is True
    assert signal.doc_overlap_ratio == 1.0
    assert signal.questions_repeat is True
    assert signal.question_similarity == 1.0
    assert signal.window_size == 3


def test_loop_signal_no_loop_when_questions_differ_despite_full_doc_overlap() -> None:
    """Single-document tenant: every coherent conversation yields docs
    overlap 1.0. Distinct questions mean the user is NOT stuck — the
    generated answer must be delivered, not thrown away (prod trace
    377ed73f-677c-40dc-8c68-15cd00a7039a)."""
    chat = _ChatStub(
        messages=[
            _user(1, "how do I install the widget"), _assistant(2, [DOC_A]),
            _user(3, "what payment methods do you accept"), _assistant(4, [DOC_A]),
            _user(5, "do you offer hosting"), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question="why does the site say hosting is included then")
    assert signal.detected is False
    assert signal.docs_repeat is True
    assert signal.doc_overlap_ratio == 1.0
    assert signal.questions_repeat is False


def test_loop_signal_no_loop_when_docs_differ_despite_repeated_question() -> None:
    """Repeated question but each answer drew on different docs — retrieval
    is still making progress, so no forced escalation."""
    chat = _ChatStub(
        messages=[
            _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A]),
            _user(3, REPEATED_QUESTION), _assistant(4, [DOC_B]),
            _user(5, REPEATED_QUESTION), _assistant(6, [DOC_C]),
        ]
    )
    signal = _signal(chat, current_question=REPEATED_QUESTION)
    assert signal.detected is False
    assert signal.docs_repeat is False
    assert signal.doc_overlap_ratio == 0.0
    assert signal.questions_repeat is True


def test_loop_signal_question_similarity_is_max_vs_prior_window() -> None:
    """The current question only needs to repeat ONE recent prior question
    (the user is orbiting back), not all of them."""
    chat = _ChatStub(
        messages=[
            _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A]),
            _user(3, "what payment methods do you accept"), _assistant(4, [DOC_A]),
            _user(5, REPEATED_QUESTION), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question=REPEATED_QUESTION)
    assert signal.detected is True
    assert signal.questions_repeat is True
    assert signal.question_similarity == 1.0


def test_loop_signal_question_similarity_is_token_based_not_exact() -> None:
    """A lightly rephrased repeat still counts: token Jaccard, not string
    equality. 'does turboflare provide web hosting' vs the same words in a
    different order is similarity 1.0; adding a couple of words keeps it
    above the 0.6 threshold."""
    chat = _ChatStub(
        messages=[
            _user(1, "does turboflare provide web hosting"), _assistant(2, [DOC_A]),
            _user(3, "does turboflare provide web hosting"), _assistant(4, [DOC_A]),
            _user(5, "does turboflare provide web hosting"), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question="web hosting does turboflare provide")
    assert signal.questions_repeat is True
    assert signal.detected is True


def test_loop_signal_shared_scaffolding_is_not_a_repeat() -> None:
    """Short questions sharing only function-word scaffolding ("how do I …")
    must not count as repeats: plain Jaccard rates 'how do I cancel' vs
    'how do I install' at 3/5 = 0.6, which would falsely escalate on a
    single-document tenant. Length-weighted Jaccard discounts the short
    scaffolding tokens and keeps the substantive verbs decisive."""
    chat = _ChatStub(
        messages=[
            _user(1, "how do i cancel"), _assistant(2, [DOC_A]),
            _user(3, "how do i upgrade"), _assistant(4, [DOC_A]),
            _user(5, "how do i pay"), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question="how do i install")
    assert signal.questions_repeat is False
    assert signal.detected is False
    assert signal.docs_repeat is True  # docs alone must not escalate


def test_loop_signal_rephrased_repeat_with_swapped_function_word() -> None:
    """Length weighting must not break rephrase tolerance: swapping one
    short function word ('my' → 'the') keeps the substantive tokens shared
    and similarity above threshold."""
    chat = _ChatStub(
        messages=[
            _user(1, "how do i cancel my subscription"), _assistant(2, [DOC_A]),
            _user(3, "how do i cancel my subscription"), _assistant(4, [DOC_A]),
            _user(5, "how do i cancel my subscription"), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question="how do i cancel the subscription")
    assert signal.questions_repeat is True
    assert signal.detected is True


def test_loop_signal_missing_question_texts_default_to_no_loop() -> None:
    """Prior user turns without content (or an empty current question) can't
    establish a repeat — safe default is to deliver the generated answer."""
    chat = _ChatStub(
        messages=[
            _user(1), _assistant(2, [DOC_A]),
            _user(3), _assistant(4, [DOC_A]),
            _user(5), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question=REPEATED_QUESTION)
    assert signal.detected is False
    assert signal.docs_repeat is True
    assert signal.questions_repeat is False
    assert signal.question_similarity == 0.0


def test_loop_signal_cyrillic_questions_are_tokenized() -> None:
    """\\w tokenization is Unicode-aware — Cyrillic repeats are detected."""
    question = "почему на сайте написано что хостинг есть"
    chat = _ChatStub(
        messages=[
            _user(1, question), _assistant(2, [DOC_A]),
            _user(3, question), _assistant(4, [DOC_A]),
            _user(5, question), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question=question)
    assert signal.detected is True
    assert signal.question_similarity == 1.0


# ---------------------------------------------------------------------------
# _compute_loop_signal — docs-overlap component (behavior preserved)
# ---------------------------------------------------------------------------


def test_loop_signal_uses_max_pairwise_overlap_not_mean() -> None:
    """A → B → A pattern: A/A overlap is 1.0 even though A/B is 0.0.
    Max-overlap heuristic catches the orbit; mean would miss it."""
    chat = _ChatStub(
        messages=[
            _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A]),
            _user(3, REPEATED_QUESTION), _assistant(4, [DOC_B]),
            _user(5, REPEATED_QUESTION), _assistant(6, [DOC_A]),
        ]
    )
    signal = _signal(chat, current_question=REPEATED_QUESTION)
    assert signal.docs_repeat is True
    assert signal.doc_overlap_ratio == 1.0
    assert signal.detected is True


def test_loop_signal_resets_when_assistant_turn_has_no_docs() -> None:
    """A greeting / small-talk / escalation handoff in the middle resets the
    loop chain — those turns aren't grounded in KB and shouldn't pollute
    the heuristic."""
    chat = _ChatStub(
        messages=[
            _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A, DOC_B]),
            _user(3, REPEATED_QUESTION), _assistant(4, [DOC_A, DOC_B]),
            _user(5, REPEATED_QUESTION), _assistant(6, []),          # reset
            _user(7, REPEATED_QUESTION), _assistant(8, [DOC_A, DOC_B]),
        ]
    )
    signal = _signal(chat, current_question=REPEATED_QUESTION)
    assert signal.detected is False
    assert signal.window_size == 1  # only the post-reset assistant turn is in the chain


@pytest.mark.parametrize(
    "min_overlap,expected",
    [(0.5, True), (0.75, False)],
)
def test_loop_signal_respects_doc_overlap_threshold(
    min_overlap: float, expected: bool
) -> None:
    """Jaccard of {A,B} vs {A,C} is 1/3 ≈ 0.33; vs {A,B} is 1.0.
    Max across pairs = 1.0, so 0.5 threshold trips, but with all three at
    Jaccard ~0.33 (no full overlap) the 0.75 threshold does not."""
    if expected:
        chat = _ChatStub(
            messages=[
                _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A, DOC_B]),
                _user(3, REPEATED_QUESTION), _assistant(4, [DOC_A, DOC_C]),
                _user(5, REPEATED_QUESTION), _assistant(6, [DOC_A, DOC_B]),
            ]
        )
    else:
        chat = _ChatStub(
            messages=[
                _user(1, REPEATED_QUESTION), _assistant(2, [DOC_A, DOC_B]),
                _user(3, REPEATED_QUESTION), _assistant(4, [DOC_A, DOC_C]),
                _user(5, REPEATED_QUESTION), _assistant(6, [DOC_A, DOC_D]),
            ]
        )
    signal = _signal(chat, current_question=REPEATED_QUESTION, min_overlap=min_overlap)
    assert signal.docs_repeat is expected
    assert signal.detected is expected  # questions repeat in both fixtures


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
