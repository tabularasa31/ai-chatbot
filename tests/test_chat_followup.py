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

from unittest.mock import Mock

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
# generate_answer slots prior_messages between system and user
# ---------------------------------------------------------------------------


def _patch_call_openai_with_retry(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    """Replace call_openai_with_retry in rag with a stub that runs the lambda
    once, records the ``messages`` it built, and returns a stable response.

    Bypasses the conftest mock_openai_client patch chain so token-accounting
    and retry behaviour can't double-count anything in CI.
    """
    from backend.chat.handlers import rag as rag_mod

    seen: list[list[dict]] = []

    def _stub_create(**kwargs):
        seen.append(kwargs.get("messages") or [])
        return Mock(
            choices=[Mock(message=Mock(content="ok answer", finish_reason="stop"))],
            usage=Mock(total_tokens=42, prompt_tokens=10, completion_tokens=32),
            model="gpt-5-mini",
        )

    class _StubClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    return _stub_create(**kwargs)

    def _retry_stub(_operation, fn, **_kwargs):
        return fn()

    from backend.chat import service as svc

    monkeypatch.setattr(svc, "get_openai_client", lambda *a, **kw: _StubClient)
    monkeypatch.setattr(rag_mod, "call_openai_with_retry", _retry_stub)
    return seen


def test_generate_answer_inserts_prior_messages_between_system_and_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.chat.handlers import rag as rag_mod

    seen = _patch_call_openai_with_retry(monkeypatch)
    monkeypatch.setattr(rag_mod, "log_llm_tokens", lambda *a, **kw: None)

    prior = [
        {"role": "user", "content": "как настроить виджет"},
        {"role": "assistant", "content": "вот как — хотите помогу с цветовой темой?"},
    ]

    answer, _tokens = rag_mod.generate_answer(
        "да",
        ["chunk about themes"],
        api_key="sk-test",
        response_language="ru",
        prior_messages=prior,
    )

    assert answer == "ok answer"
    assert len(seen) == 1
    sent = seen[0]
    assert sent[0]["role"] == "system"
    assert sent[1] == prior[0]
    assert sent[2] == prior[1]
    assert sent[-1]["role"] == "user"
    assert "Question: да" in sent[-1]["content"]


def test_generate_answer_without_prior_messages_keeps_legacy_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.chat.handlers import rag as rag_mod

    seen = _patch_call_openai_with_retry(monkeypatch)
    monkeypatch.setattr(rag_mod, "log_llm_tokens", lambda *a, **kw: None)

    rag_mod.generate_answer(
        "цена подписки?",
        ["chunk about pricing"],
        api_key="sk-test",
        response_language="ru",
    )

    assert len(seen) == 1
    assert [m["role"] for m in seen[0]] == ["system", "user"]
