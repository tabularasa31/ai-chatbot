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


# ---------------------------------------------------------------------------
# detect_tenant_kb_script
# ---------------------------------------------------------------------------

class TestDetectTenantKbScript:
    """detect_tenant_kb_script() reads Document.language stored at parse time."""

    @staticmethod
    def _mock_db_with_languages(languages):
        """Build a MagicMock db whose Document.language query returns ``languages``."""
        from unittest.mock import MagicMock

        rows = [(lang,) for lang in languages]
        mock_db = MagicMock()
        # Path used by _kb_bucket_counts_from_languages:
        # db.query(Document.language).filter(...).filter(...).all()
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = rows
        return mock_db

    def _clear_caches(self, tenant_id):
        from backend.search.service import (
            _TENANT_KB_SCRIPT_CACHE,
            _TENANT_KB_SCRIPTS_CACHE,
        )

        _TENANT_KB_SCRIPT_CACHE.pop(str(tenant_id), None)
        _TENANT_KB_SCRIPTS_CACHE.pop(str(tenant_id), None)

    def test_returns_cyrillic_for_russian_documents(self):
        import uuid
        from backend.search.service import detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)
        mock_db = self._mock_db_with_languages(["ru", "ru", "ru"])

        assert detect_tenant_kb_script(tenant_id, mock_db) == "cyrillic"

    def test_returns_latin_for_english_documents(self):
        import uuid
        from backend.search.service import detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)
        mock_db = self._mock_db_with_languages(["en", "en"])

        assert detect_tenant_kb_script(tenant_id, mock_db) == "latin"

    def test_dominant_wins_for_mixed_kb(self):
        """Dominant bucket is returned even when KB has multiple scripts."""
        import uuid
        from backend.search.service import detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)
        mock_db = self._mock_db_with_languages(["en", "en", "en", "ru"])

        assert detect_tenant_kb_script(tenant_id, mock_db) == "latin"

    def test_returns_none_for_empty_kb(self):
        from unittest.mock import MagicMock
        import uuid
        from backend.search.service import detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)

        mock_db = MagicMock()
        # No documents with language set, no chunks indexed either.
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.join.return_value.filter.return_value.limit.return_value.all.return_value = []

        assert detect_tenant_kb_script(tenant_id, mock_db) is None

    def test_falls_back_to_chunk_sampling_when_no_language_set(self):
        """Legacy KBs (Document.language all NULL) fall back to chunk sampling."""
        from unittest.mock import MagicMock
        import uuid
        from backend.search.service import detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)

        mock_db = MagicMock()
        # Language path returns no rows → fallback kicks in
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        # Chunk-sampling path returns Russian text
        mock_db.query.return_value.join.return_value.filter.return_value.limit.return_value.all.return_value = [
            ("Сайт не открывается после подключения к CDN.",),
            ("Проверьте NS-пропагацию.",),
        ]

        assert detect_tenant_kb_script(tenant_id, mock_db) == "cyrillic"

    def test_uses_cache_on_second_call(self):
        import uuid
        from backend.search.service import detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)
        mock_db = self._mock_db_with_languages(["ru"])

        detect_tenant_kb_script(tenant_id, mock_db)
        detect_tenant_kb_script(tenant_id, mock_db)

        # DB should be queried only once — second call hits cache
        assert mock_db.query.call_count == 1


class TestDetectTenantKbScripts:
    """detect_tenant_kb_scripts() returns the full set of buckets in the KB."""

    @staticmethod
    def _mock_db_with_languages(languages):
        from unittest.mock import MagicMock

        rows = [(lang,) for lang in languages]
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = rows
        return mock_db

    def _clear_caches(self, tenant_id):
        from backend.search.service import (
            _TENANT_KB_SCRIPT_CACHE,
            _TENANT_KB_SCRIPTS_CACHE,
        )

        _TENANT_KB_SCRIPT_CACHE.pop(str(tenant_id), None)
        _TENANT_KB_SCRIPTS_CACHE.pop(str(tenant_id), None)

    def test_mixed_kb_returns_both_buckets(self):
        """EN+RU KB → {cyrillic, latin}, so cross-lingual rewrite reaches both."""
        import uuid
        from backend.search.service import detect_tenant_kb_scripts

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)
        mock_db = self._mock_db_with_languages(["en", "ru", "en"])

        assert detect_tenant_kb_scripts(tenant_id, mock_db) == frozenset(
            {"cyrillic", "latin"}
        )

    def test_single_language_returns_single_bucket(self):
        import uuid
        from backend.search.service import detect_tenant_kb_scripts

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)
        mock_db = self._mock_db_with_languages(["en", "es", "fr"])

        assert detect_tenant_kb_scripts(tenant_id, mock_db) == frozenset({"latin"})

    def test_empty_kb_returns_empty_set(self):
        from unittest.mock import MagicMock
        import uuid
        from backend.search.service import detect_tenant_kb_scripts

        tenant_id = uuid.uuid4()
        self._clear_caches(tenant_id)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.join.return_value.filter.return_value.limit.return_value.all.return_value = []

        assert detect_tenant_kb_scripts(tenant_id, mock_db) == frozenset()


# ---------------------------------------------------------------------------
# semantic_query_rewrite_for_kb
# ---------------------------------------------------------------------------

class TestSemanticQueryRewriteForKb:
    """semantic_query_rewrite_for_kb() produces a KB-language rewrite."""

    def test_returns_russian_rewrite_for_cyrillic_kb(self):
        from backend.search.service import semantic_query_rewrite_for_kb

        with (
            patch("backend.search.service.get_openai_client") as mock_client,
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.return_value = _make_openai_response(
                "диагностика подключения CDN NS-пропагация A-запись SSL"
            )
            mock_client.return_value = MagicMock()

            result = semantic_query_rewrite_for_kb(
                "The site doesn't open after connecting — what should I check?",
                kb_script="cyrillic",
                api_key="sk-test",
            )

        assert result == "диагностика подключения CDN NS-пропагация A-запись SSL"

    def test_returns_none_for_unknown_script(self):
        from backend.search.service import semantic_query_rewrite_for_kb

        result = semantic_query_rewrite_for_kb(
            "Some question",
            kb_script="other",
            api_key="sk-test",
        )
        assert result is None

    def test_returns_none_on_llm_failure(self):
        from backend.search.service import semantic_query_rewrite_for_kb

        with (
            patch("backend.search.service.get_openai_client") as mock_client,
            patch("backend.search.service.call_openai_with_retry") as mock_retry,
        ):
            mock_retry.side_effect = RuntimeError("LLM error")
            mock_client.return_value = MagicMock()

            result = semantic_query_rewrite_for_kb(
                "The site doesn't open after connecting",
                kb_script="cyrillic",
                api_key="sk-test",
            )

        assert result is None


# ---------------------------------------------------------------------------
# _bm25_queries_for_script — non-EN now includes original query
# ---------------------------------------------------------------------------

class TestBm25QueriesForScriptNonEn:
    """Non-EN queries: order depends on whether KB script matches query script."""

    def test_same_script_kb_puts_original_first(self):
        """Cyrillic query + Cyrillic KB → original first for same-language BM25."""
        from backend.search.service import _bm25_queries_for_script

        query = "сайт не открывается после подключения"
        en_rewrite = "site connectivity troubleshooting"
        variants = [query, en_rewrite]
        result = _bm25_queries_for_script(query, variants, "cyrillic", kb_script="cyrillic")

        assert result[0] == query, "Original must be first when KB is same-script"
        assert en_rewrite in result

    def test_cross_script_kb_puts_en_rewrite_first(self):
        """Cyrillic query + Latin KB → EN rewrite first so asymmetric BM25 uses it."""
        from backend.search.service import _bm25_queries_for_script

        query = "сайт не открывается"
        en_rewrite = "CDN site connectivity troubleshooting"
        variants = [query, en_rewrite]
        result = _bm25_queries_for_script(query, variants, "cyrillic", kb_script="latin")

        assert result[0] == en_rewrite, "EN rewrite must be first for cross-script KB"
        assert query in result

    def test_unknown_kb_script_puts_en_rewrite_first(self):
        """No kb_script → EN rewrite first (safe default for unknown KB language)."""
        from backend.search.service import _bm25_queries_for_script

        query = "сайт не открывается"
        en_rewrite = "CDN site connectivity troubleshooting"
        variants = [query, en_rewrite]
        result = _bm25_queries_for_script(query, variants, "cyrillic")

        assert result[0] == en_rewrite

    def test_en_query_unchanged(self):
        from backend.search.service import _bm25_queries_for_script

        query = "site does not open after connecting"
        result = _bm25_queries_for_script(query, [query], "latin")

        assert result == [query]
