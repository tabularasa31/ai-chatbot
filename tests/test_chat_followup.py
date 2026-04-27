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
from typing import Any

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


class _CapturingClient:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

        class _Chat:
            def __init__(_self, outer: "_CapturingClient") -> None:
                _self._outer = outer

                class _Completions:
                    def create(_self_inner, **kwargs: Any):
                        outer.calls.append(kwargs["messages"])
                        msg = SimpleNamespace(content="ok answer")
                        choice = SimpleNamespace(message=msg)
                        usage = SimpleNamespace(total_tokens=42)
                        return SimpleNamespace(
                            choices=[choice], usage=usage
                        )

                _self.completions = _Completions()

        self.chat = _Chat(self)


def test_generate_answer_inserts_prior_messages_between_system_and_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.chat import service as svc
    from backend.chat.handlers import rag as rag_mod

    captured = _CapturingClient()
    monkeypatch.setattr(svc, "get_openai_client", lambda *a, **kw: captured)

    # Avoid Langfuse / metrics side-effects.
    monkeypatch.setattr(rag_mod, "log_llm_tokens", lambda *a, **kw: None)

    prior = [
        {"role": "user", "content": "как настроить виджет"},
        {"role": "assistant", "content": "вот как — хотите помогу с цветовой темой?"},
    ]

    answer, tokens = rag_mod.generate_answer(
        "да",
        ["chunk about themes"],
        api_key="sk-test",
        response_language="ru",
        prior_messages=prior,
    )

    assert answer == "ok answer"
    assert tokens == 42
    assert len(captured.calls) == 1
    sent = captured.calls[0]
    assert sent[0]["role"] == "system"
    assert sent[1] == prior[0]
    assert sent[2] == prior[1]
    assert sent[-1]["role"] == "user"
    assert "Question: да" in sent[-1]["content"]


def test_generate_answer_without_prior_messages_keeps_legacy_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.chat import service as svc
    from backend.chat.handlers import rag as rag_mod

    captured = _CapturingClient()
    monkeypatch.setattr(svc, "get_openai_client", lambda *a, **kw: captured)
    monkeypatch.setattr(rag_mod, "log_llm_tokens", lambda *a, **kw: None)

    rag_mod.generate_answer(
        "цена подписки?",
        ["chunk about pricing"],
        api_key="sk-test",
        response_language="ru",
    )

    sent = captured.calls[0]
    assert [m["role"] for m in sent] == ["system", "user"]
