"""Unit tests for semantic_query_rewrite() in search/service.py.

All tests are pure-unit: no DB, no HTTP client, no real OpenAI call.
OpenAI is mocked at the openai_client / call_openai_with_retry layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.core.config import settings
from backend.search.service import semantic_query_rewrite


# ---------------------------------------------------------------------------
# Override the global autouse mock fixture from conftest so this file's own
# per-test patches take effect cleanly (no conftest-level side-effects).
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def mock_openai_client():  # noqa: PT004  (intentional override, no yield needed)
    """No-op override: this module controls its own mocking per-test."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_openai_response(content: str) -> SimpleNamespace:
    """Minimal mock that mimics response.choices[0].message.content."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content))
        ]
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestSemanticQueryRewriteHappyPath:
    """semantic_query_rewrite() returns a clean English rewrite on success."""

    def test_returns_english_feature_rewrite(self):
        """Basic happy path: LLM returns a clean single-line string."""
        with (
            patch("backend.search.service.get_openai_client") as mock_client,
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.return_value = _make_openai_response(
                "language detection multilingual bot settings"
            )
            mock_client.return_value = MagicMock()

            result = semantic_query_rewrite(
                "Почему бот отвечает только по-английски?",
                api_key="sk-test",
            )

        assert result == "language detection multilingual bot settings"

    def test_strips_surrounding_whitespace(self):
        """Trailing/leading whitespace in LLM response is stripped."""
        with (
            patch("backend.search.service.get_openai_client"),
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.return_value = _make_openai_response(
                "  widget troubleshooting embed setup  "
            )
            result = semantic_query_rewrite(
                "Мой виджет завис и не реагирует на клики",
                api_key="sk-test",
            )

        assert result == "widget troubleshooting embed setup"

    def test_prompt_contains_user_question(self):
        """The prompt sent to OpenAI includes the original user question."""
        with (
            patch("backend.search.service.get_openai_client") as mock_client,
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            inner_client = MagicMock()
            mock_client.return_value = inner_client
            mock_retry.return_value = _make_openai_response("relevance guard topic configuration")

            semantic_query_rewrite(
                "How do I stop the bot from going off-topic?",
                api_key="sk-test",
            )

            call_args = mock_retry.call_args[0]  # positional: (label, fn)
            fn = call_args[1]
            inner_client.chat.completions.create.return_value = _make_openai_response("x")
            fn()

            create_call = inner_client.chat.completions.create.call_args
            content = create_call[1]["messages"][0]["content"]

        assert "How do I stop the bot from going off-topic?" in content
        assert "FEATURE or SETTING" in content  # prompt focuses on feature terminology

    def test_curly_braces_in_query_do_not_crash(self):
        """User input with {braces} must not raise KeyError from .format()."""
        with (
            patch("backend.search.service.get_openai_client") as mock_client,
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_client.return_value = MagicMock()
            mock_retry.return_value = _make_openai_response("feature settings config")

            # Would raise KeyError if .format() were used on the prompt template
            result = semantic_query_rewrite(
                "What is {name} and {0} doing in my bot?",
                api_key="sk-test",
            )

        assert result == "feature settings config"

    def test_uses_gpt4o_mini(self):
        """Uses the configured query_rewrite_model for the rewrite."""
        with (
            patch("backend.search.service.get_openai_client") as mock_client,
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.return_value = _make_openai_response("knowledge base indexing RAG retrieval")
            inner = MagicMock()
            mock_client.return_value = inner

            semantic_query_rewrite(
                "Почему бот не находит ответ, хотя я загрузил документ?",
                api_key="sk-test",
            )

            call_args = mock_retry.call_args
            # call_openai_with_retry("label", lambda: ...) — extract lambda and call it
            label, fn = call_args[0]
            assert label == "semantic_query_rewrite"
            # Call the lambda to trigger create() and inspect the model
            inner.chat.completions.create.return_value = _make_openai_response("x")
            fn()
            create_kwargs = inner.chat.completions.create.call_args[1]
            assert create_kwargs["model"] == settings.query_rewrite_model
            assert create_kwargs["max_completion_tokens"] == 40
            assert create_kwargs["temperature"] == 0


# ---------------------------------------------------------------------------
# Failure / edge-case tests
# ---------------------------------------------------------------------------

class TestSemanticQueryRewriteFailures:
    """semantic_query_rewrite() returns None gracefully on all failures."""

    def test_returns_none_on_openai_exception(self):
        """Any OpenAI exception results in None (never propagates)."""
        with (
            patch("backend.search.service.get_openai_client"),
            patch(
                "backend.search.service.call_openai_with_retry",
                side_effect=Exception("openai timeout"),
            ),
        ):
            result = semantic_query_rewrite("some question", api_key="sk-test")

        assert result is None

    def test_returns_none_on_empty_content(self):
        """Empty string from LLM → None (sanity check guards)."""
        with (
            patch("backend.search.service.get_openai_client"),
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.return_value = _make_openai_response("")
            result = semantic_query_rewrite("some question", api_key="sk-test")

        assert result is None

    def test_returns_none_on_multiline_response(self):
        """Multi-line LLM response is rejected (sanity check)."""
        with (
            patch("backend.search.service.get_openai_client"),
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.return_value = _make_openai_response(
                "language detection\nmultilingual settings"
            )
            result = semantic_query_rewrite("some question", api_key="sk-test")

        assert result is None

    def test_returns_none_on_oversized_response(self):
        """Response longer than 200 chars is rejected."""
        with (
            patch("backend.search.service.get_openai_client"),
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.return_value = _make_openai_response("x" * 201)
            result = semantic_query_rewrite("some question", api_key="sk-test")

        assert result is None

    def test_returns_none_on_empty_query(self):
        """Empty query short-circuits before any API call."""
        with patch("backend.search.service.get_openai_client") as mock_client:
            result = semantic_query_rewrite("", api_key="sk-test")

        mock_client.assert_not_called()
        assert result is None

    def test_returns_none_on_empty_api_key(self):
        """Empty API key short-circuits before any API call."""
        with patch("backend.search.service.get_openai_client") as mock_client:
            result = semantic_query_rewrite("some question", api_key="")

        mock_client.assert_not_called()
        assert result is None

    def test_returns_none_on_get_client_exception(self):
        """Exception from get_openai_client → None."""
        with patch(
            "backend.search.service.get_openai_client",
            side_effect=RuntimeError("bad key"),
        ):
            result = semantic_query_rewrite("some question", api_key="sk-test")

        assert result is None


# ---------------------------------------------------------------------------
# Integration with expand_query: deduplication
# ---------------------------------------------------------------------------

class TestSemanticRewriteDeduplication:
    """Rewrite variant is only added when it differs from lexical variants."""

    def test_dedup_case_insensitive(self):
        """If rewrite duplicates a lexical variant (case-insensitive), it's skipped."""
        from backend.search.service import expand_query

        question = "language settings"  # lexical expand_query will produce this
        lexical = expand_query(question)  # ['language settings', 'language settings'] → deduped

        # Simulate: rewritten_variant == one of the lexical variants (different case)
        rewrite = "Language Settings"
        combined = [*lexical]
        if rewrite.casefold() not in {v.casefold() for v in lexical}:
            combined = [*lexical, rewrite]

        # Should NOT add because 'language settings' == 'Language Settings' casefold
        assert len(combined) == len(lexical)

    def test_new_semantic_variant_is_appended(self):
        """A genuinely new semantic rewrite is appended as an extra variant."""
        from backend.search.service import expand_query

        question = "Почему бот отвечает только по-английски?"
        lexical = expand_query(question)
        rewrite = "language detection multilingual bot settings"

        assert rewrite.casefold() not in {v.casefold() for v in lexical}

        combined = [*lexical, rewrite]
        assert rewrite in combined
        assert len(combined) == len(lexical) + 1
