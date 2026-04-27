"""Tests for the dialogue follow-up bridge.

Covers:
  * ``looks_like_short_followup`` heuristic for ru/en affirmations and short
    replies, with negative cases for normal questions.
  * ``build_contextual_retrieval_query`` stitches the last assistant tail with
    the current user reply and returns ``None`` when no assistant context
    exists.
  * ``generate_answer`` inserts ``prior_messages`` between the system prompt
    and the current user message before calling OpenAI.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.chat.followup import (
    build_contextual_retrieval_query,
    looks_like_short_followup,
)
from backend.models import MessageRole


class _StubMessage:
    def __init__(self, role: MessageRole, content: str, *, idx: int = 0):
        self.role = role
        self.content = content
        self.id = idx
        self.created_at = None


# ---------------------------------------------------------------------------
# looks_like_short_followup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "да",
        "Да!",
        "ок",
        "ну да",
        "yes",
        "yes please",
        "ok",
        "go ahead",
        "конечно",
        "пожалуйста",
        "давай",
    ],
)
def test_looks_like_short_followup_positive(text: str) -> None:
    assert looks_like_short_followup(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "как настроить виджет",
        "what is the pricing",
        "сколько стоит подписка для команды",
        "Tell me about your refund policy",
        "",
        "   ",
    ],
)
def test_looks_like_short_followup_negative(text: str) -> None:
    assert looks_like_short_followup(text) is False


# ---------------------------------------------------------------------------
# build_contextual_retrieval_query
# ---------------------------------------------------------------------------


def test_contextual_query_combines_assistant_tail_and_user_reply() -> None:
    messages = [
        _StubMessage(MessageRole.user, "как настроить виджет?", idx=1),
        _StubMessage(
            MessageRole.assistant,
            "Виджет настраивается через Settings → Widget. "
            "Хотите помогу с цветовой темой?",
            idx=2,
        ),
    ]
    out = build_contextual_retrieval_query(messages, "да")
    assert out is not None
    assert "цветовой темой" in out
    assert out.endswith("\nда")


def test_contextual_query_picks_latest_assistant_when_messages_out_of_order() -> None:
    # Chat.messages has no DB ``order_by``; the helper must not blindly trust
    # iteration order. Build a list where the older assistant message comes
    # AFTER the newer one and confirm the latest tail (by created_at) wins.
    from datetime import datetime, timedelta

    base = datetime(2026, 1, 1, 12, 0, 0)
    older_assistant = _StubMessage(
        MessageRole.assistant,
        "Старый ответ. Хотите старую тему?",
        idx=1,
    )
    older_assistant.created_at = base
    newer_assistant = _StubMessage(
        MessageRole.assistant,
        "Свежий ответ. Хотите помогу с цветовой темой?",
        idx=2,
    )
    newer_assistant.created_at = base + timedelta(minutes=5)
    out = build_contextual_retrieval_query(
        [newer_assistant, older_assistant],  # intentionally reversed
        "да",
    )
    assert out is not None
    assert "цветовой темой" in out
    assert "старую тему" not in out


def test_contextual_query_returns_none_without_assistant_history() -> None:
    messages = [_StubMessage(MessageRole.user, "первый ход", idx=1)]
    assert build_contextual_retrieval_query(messages, "да") is None


def test_contextual_query_caps_long_assistant_tail() -> None:
    long_text = "x" * 2000 + " Хотите помогу с настройкой?"
    messages = [_StubMessage(MessageRole.assistant, long_text, idx=1)]
    out = build_contextual_retrieval_query(messages, "да")
    assert out is not None
    # Tail cap (400) + reply length, well under the original 2k+ assistant text.
    assert len(out) < 600
    assert "Хотите помогу с настройкой?" in out
    assert out.endswith("\nда")


# ---------------------------------------------------------------------------
# _assemble_chat_messages: system → prior_messages → current user
# ---------------------------------------------------------------------------


def test_assemble_chat_messages_inserts_prior_between_system_and_user() -> None:
    from backend.chat.handlers.rag import _assemble_chat_messages

    prior = [
        {"role": "user", "content": "как настроить виджет"},
        {"role": "assistant", "content": "вот как — хотите помогу с темой?"},
    ]
    out = _assemble_chat_messages(
        system_prompt="SYS",
        user_message="Question: да",
        prior_messages=prior,
    )
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == prior[0]
    assert out[2] == prior[1]
    assert out[-1] == {"role": "user", "content": "Question: да"}


def test_assemble_chat_messages_without_prior_keeps_legacy_shape() -> None:
    from backend.chat.handlers.rag import _assemble_chat_messages

    out = _assemble_chat_messages(
        system_prompt="SYS",
        user_message="Question: цена?",
        prior_messages=None,
    )
    assert [m["role"] for m in out] == ["system", "user"]


def test_assemble_chat_messages_empty_prior_treated_as_none() -> None:
    from backend.chat.handlers.rag import _assemble_chat_messages

    out = _assemble_chat_messages(
        system_prompt="SYS",
        user_message="Q",
        prior_messages=[],
    )
    assert [m["role"] for m in out] == ["system", "user"]


# ---------------------------------------------------------------------------
# _build_prior_messages_for_llm: trims, caps, filters empties
# ---------------------------------------------------------------------------


def test_build_prior_messages_for_llm_trims_to_max_messages_and_caps_chars() -> None:
    from datetime import datetime, timedelta

    from backend.chat.handlers.rag import _build_prior_messages_for_llm

    base = datetime(2026, 1, 1, 12, 0, 0)
    msgs = []
    for i, role in enumerate(
        [
            MessageRole.user,
            MessageRole.assistant,
            MessageRole.user,
            MessageRole.assistant,
        ]
    ):
        m = _StubMessage(role, "x" * 200 if i == 3 else f"msg{i}", idx=i + 1)
        m.created_at = base + timedelta(seconds=i)
        msgs.append(m)
    chat_stub = SimpleNamespace(messages=msgs)
    out = _build_prior_messages_for_llm(chat_stub, max_messages=2, char_cap=50)
    # Last 2 (msg2 and the long assistant text) win; long one is capped.
    assert len(out) == 2
    assert out[0]["role"] == "user" and out[0]["content"] == "msg2"
    assert out[1]["role"] == "assistant"
    assert out[1]["content"].endswith("…")
    assert len(out[1]["content"]) <= 51  # 50 chars + ellipsis


def test_build_prior_messages_for_llm_returns_none_for_empty_chat() -> None:
    from backend.chat.handlers.rag import _build_prior_messages_for_llm

    assert _build_prior_messages_for_llm(None, max_messages=6, char_cap=1500) is None
    chat_stub = SimpleNamespace(messages=[])
    assert _build_prior_messages_for_llm(chat_stub, max_messages=6, char_cap=1500) is None


def test_build_prior_messages_for_llm_skips_empty_content() -> None:
    from datetime import datetime

    from backend.chat.handlers.rag import _build_prior_messages_for_llm

    base = datetime(2026, 1, 1)
    blank = _StubMessage(MessageRole.user, "   ", idx=1)
    blank.created_at = base
    real = _StubMessage(MessageRole.assistant, "real reply", idx=2)
    real.created_at = base
    chat_stub = SimpleNamespace(messages=[blank, real])
    out = _build_prior_messages_for_llm(chat_stub, max_messages=6, char_cap=1500)
    assert out == [{"role": "assistant", "content": "real reply"}]
