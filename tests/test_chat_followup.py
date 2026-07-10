"""Tests for the dialog-context bridge.

Covers:
  * ``build_dialog_context`` renders the recent exchanges for per-turn LLM
    helpers (relevance guard, history-aware query rewrite), keeping the tail
    of assistant messages so the bot's trailing follow-up question survives
    truncation.
  * ``generate_answer`` inserts ``prior_messages`` between the system prompt
    and the current user message before calling OpenAI.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.models import MessageRole


class _StubMessage:
    def __init__(self, role: MessageRole, content: str, *, idx: int = 0):
        self.role = role
        self.content = content
        self.id = idx
        self.created_at = None


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


# ---------------------------------------------------------------------------
# build_dialog_context
# ---------------------------------------------------------------------------


def test_build_dialog_context_renders_last_turns_in_order() -> None:
    from backend.chat.followup import build_dialog_context

    messages = [
        _StubMessage(MessageRole.user, "old question", idx=1),
        _StubMessage(MessageRole.assistant, "old answer", idx=2),
        _StubMessage(MessageRole.user, "how do I set up SSL?", idx=3),
        _StubMessage(MessageRole.assistant, "Upload a certificate.", idx=4),
    ]
    ctx = build_dialog_context(messages, max_turns=1)
    assert ctx == "User: how do I set up SSL?\nAssistant: Upload a certificate."


def test_build_dialog_context_caps_message_length() -> None:
    from backend.chat.followup import build_dialog_context

    messages = [
        _StubMessage(MessageRole.user, "q", idx=1),
        _StubMessage(MessageRole.assistant, "a" * 1000, idx=2),
    ]
    ctx = build_dialog_context(messages, char_cap=50)
    assert ctx is not None
    for line in ctx.splitlines():
        assert len(line) <= 50 + len("Assistant: ")


def test_build_dialog_context_keeps_assistant_tail_question() -> None:
    # The bot's follow-up question sits at the END of its reply; truncation
    # must keep the tail, or continuation resolution ("да, как проверить?")
    # loses exactly the sentence it needs.
    from backend.chat.followup import build_dialog_context

    long_answer = "x" * 2000 + " Хотите помогу с настройкой делегации?"
    messages = [
        _StubMessage(MessageRole.user, "как подключить домен?", idx=1),
        _StubMessage(MessageRole.assistant, long_answer, idx=2),
    ]
    ctx = build_dialog_context(messages)
    assert ctx is not None
    assert "Хотите помогу с настройкой делегации?" in ctx


def test_build_dialog_context_keeps_user_head() -> None:
    # User messages state the topic up front — keep the head on truncation.
    from backend.chat.followup import build_dialog_context

    long_question = "как подключить домен к виджету " + "и " * 500
    messages = [
        _StubMessage(MessageRole.user, long_question, idx=1),
        _StubMessage(MessageRole.assistant, "ответ", idx=2),
    ]
    ctx = build_dialog_context(messages)
    assert ctx is not None
    assert "как подключить домен к виджету" in ctx


def test_build_dialog_context_orders_by_created_at_not_list_order() -> None:
    # Chat.messages has no DB ``order_by``; the helper must sort by
    # created_at instead of trusting iteration order.
    from datetime import datetime, timedelta

    from backend.chat.followup import build_dialog_context

    base = datetime(2026, 1, 1, 12, 0, 0)
    newer = _StubMessage(MessageRole.assistant, "свежий ответ", idx=2)
    newer.created_at = base + timedelta(minutes=5)
    older = _StubMessage(MessageRole.user, "старый вопрос", idx=1)
    older.created_at = base
    ctx = build_dialog_context([newer, older])  # intentionally reversed
    assert ctx == "User: старый вопрос\nAssistant: свежий ответ"


def test_build_dialog_context_empty_history_returns_none() -> None:
    from backend.chat.followup import build_dialog_context

    assert build_dialog_context([]) is None
    assert build_dialog_context([_StubMessage(MessageRole.user, "   ", idx=1)]) is None
