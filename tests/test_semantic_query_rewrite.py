"""Unit tests for async_semantic_query_rewrite() in search/service.py.

All tests are pure-unit: no DB, no HTTP client, no real OpenAI call.
OpenAI is mocked at the get_async_openai_client / async_call_openai_with_retry
layer; DB-backed helpers get a mock AsyncSession whose execute() routes by
the selected column.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.config import settings
from backend.search.service import async_semantic_query_rewrite


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


def _patch_rewrite_layer():
    """Patch the async client factory and retry helper used by the rewrite.

    Returns the context managers as a tuple suitable for ``with (...)``.
    The retry helper is an AsyncMock because production code awaits it.
    """
    return (
        patch("backend.search.query_variants.get_async_openai_client"),
        patch(
            "backend.search.query_variants.async_call_openai_with_retry",
            new_callable=AsyncMock,
        ),
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestSemanticQueryRewriteHappyPath:
    """async_semantic_query_rewrite() returns a clean English rewrite on success."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("question", "llm_content", "expected"),
        [
            pytest.param(
                "Почему бот отвечает только по-английски?",
                "language detection multilingual bot settings",
                "language detection multilingual bot settings",
                id="basic_english_rewrite",
            ),
            pytest.param(
                "Мой виджет завис и не реагирует на клики",
                "  widget troubleshooting embed setup  ",
                "widget troubleshooting embed setup",
                id="strips_surrounding_whitespace",
            ),
            pytest.param(
                "What is {name} and {0} doing in my bot?",
                "feature settings config",
                "feature settings config",
                id="curly_braces_in_query_do_not_crash",
            ),
        ],
    )
    async def test_returns_clean_rewrite(self, question, llm_content, expected):
        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            mock_retry.return_value = _make_openai_response(llm_content)
            mock_client.return_value = MagicMock()

            # Would raise KeyError if .format() were used on the prompt template.
            result = await async_semantic_query_rewrite(question, api_key="sk-test")

        assert result == expected

    @pytest.mark.asyncio
    async def test_prompt_contains_user_question(self):
        """The prompt sent to OpenAI includes the original user question."""
        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            inner_client = MagicMock()
            mock_client.return_value = inner_client
            mock_retry.return_value = _make_openai_response("relevance guard topic configuration")

            await async_semantic_query_rewrite(
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

    @pytest.mark.asyncio
    async def test_uses_gpt4o_mini(self):
        """Uses the configured query_rewrite_model for the rewrite."""
        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            mock_retry.return_value = _make_openai_response("knowledge base indexing RAG retrieval")
            inner = MagicMock()
            mock_client.return_value = inner

            await async_semantic_query_rewrite(
                "Почему бот не находит ответ, хотя я загрузил документ?",
                api_key="sk-test",
            )

            call_args = mock_retry.call_args
            # async_call_openai_with_retry("label", lambda: ...) — extract lambda
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
# Dialog-context (history-aware) prompt variant
# ---------------------------------------------------------------------------

class TestSemanticQueryRewriteDialogContext:
    """With dialog_context the prompt lets the model resolve continuations."""

    @staticmethod
    def _sent_prompt(mock_retry, inner_client) -> str:
        """Extract the prompt content from the lambda handed to the retry helper."""
        fn = mock_retry.call_args[0][1]
        inner_client.chat.completions.create.return_value = _make_openai_response("x")
        fn()
        return inner_client.chat.completions.create.call_args[1]["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_prompt_includes_dialog_and_resolution_instruction(self):
        dialog = (
            "User: как подключить домен?\n"
            "Assistant: Настройте делегацию. Хотите помогу проверить?"
        )
        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            inner = MagicMock()
            mock_client.return_value = inner
            mock_retry.return_value = _make_openai_response(
                "проверка настройки делегации домена"
            )

            result = await async_semantic_query_rewrite(
                "да, как проверить?",
                api_key="sk-test",
                dialog_context=dialog,
            )
            content = self._sent_prompt(mock_retry, inner)

        assert result == "проверка настройки делегации домена"
        assert dialog in content
        assert "да, как проверить?" in content
        # The model — not a heuristic — decides continuation vs standalone.
        assert "self-contained, ignore the conversation" in content
        assert "FEATURE or SETTING" in content

    @pytest.mark.asyncio
    async def test_prompt_without_dialog_context_is_unchanged(self):
        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            inner = MagicMock()
            mock_client.return_value = inner
            mock_retry.return_value = _make_openai_response("widget setup embed")

            await async_semantic_query_rewrite(
                "как настроить виджет",
                api_key="sk-test",
                dialog_context=None,
            )
            content = self._sent_prompt(mock_retry, inner)

        assert content.startswith("A customer asked a product support chatbot:")
        assert "conversation" not in content

    @pytest.mark.asyncio
    async def test_curly_braces_in_dialog_do_not_crash(self):
        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            mock_client.return_value = MagicMock()
            mock_retry.return_value = _make_openai_response("feature settings config")

            result = await async_semantic_query_rewrite(
                "yes {please}",
                api_key="sk-test",
                dialog_context="Assistant: use {placeholders} like {0}",
            )

        assert result == "feature settings config"


# ---------------------------------------------------------------------------
# Failure / edge-case tests
# ---------------------------------------------------------------------------

class TestSemanticQueryRewriteFailures:
    """async_semantic_query_rewrite() returns None gracefully on all failures."""

    @pytest.mark.asyncio
    async def test_returns_none_on_openai_exception(self):
        """Any OpenAI exception results in None (never propagates)."""
        with (
            patch("backend.search.query_variants.get_async_openai_client"),
            patch(
                "backend.search.query_variants.async_call_openai_with_retry",
                new_callable=AsyncMock,
                side_effect=Exception("openai timeout"),
            ),
        ):
            result = await async_semantic_query_rewrite("some question", api_key="sk-test")

        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("llm_content", "reason"),
        [
            pytest.param("", "empty_content", id="empty_content"),
            pytest.param(
                "language detection\nmultilingual settings",
                "multiline_response",
                id="multiline_response",
            ),
            pytest.param("x" * 201, "oversized_response", id="oversized_response"),
        ],
    )
    async def test_returns_none_on_rejected_content(self, llm_content, reason):
        """Sanity-check guards on the LLM response shape: empty, multi-line, or
        oversized content is rejected rather than trusted as a rewrite."""
        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch, retry_patch as mock_retry:
            mock_retry.return_value = _make_openai_response(llm_content)
            result = await async_semantic_query_rewrite("some question", api_key="sk-test")

        assert result is None, reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("question", "api_key"),
        [
            pytest.param("", "sk-test", id="empty_query"),
            pytest.param("some question", "", id="empty_api_key"),
        ],
    )
    async def test_returns_none_and_skips_api_call_on_empty_argument(self, question, api_key):
        """Empty query or empty API key short-circuits before any API call."""
        with patch("backend.search.query_variants.get_async_openai_client") as mock_client:
            result = await async_semantic_query_rewrite(question, api_key=api_key)

        mock_client.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_get_client_exception(self):
        """Exception from get_async_openai_client → None."""
        with patch(
            "backend.search.query_variants.get_async_openai_client",
            side_effect=RuntimeError("bad key"),
        ):
            result = await async_semantic_query_rewrite("some question", api_key="sk-test")

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
# async_detect_tenant_kb_script
# ---------------------------------------------------------------------------

def _make_async_db(
    *,
    script_rows: list[tuple[str, int]] | None = None,
    has_unlabeled: bool = False,
    chunk_rows: list[tuple[str]] | None = None,
) -> MagicMock:
    """Mock AsyncSession whose execute() routes by the selected column.

    The async KB-script helpers issue three distinct statements:
    - ``select(Document.script, count) group by`` → per-script document
      counts (``.all()``)
    - ``select(Document.id) ... script IS NULL limit 1`` → unlabeled
      presence probe (``.scalar()``)
    - ``select(Embedding.chunk_text) join ...`` → legacy chunk sampling
      (``.all()``)

    ``db.executed_columns`` records which paths actually ran so tests can
    assert e.g. that chunk sampling was skipped.
    """
    scripts = script_rows or []
    chunks = chunk_rows or []
    db = MagicMock()
    db.executed_columns = []

    async def _execute(stmt):
        column = list(stmt.selected_columns)[0].key
        db.executed_columns.append(column)
        result = MagicMock()
        if column == "script":
            result.all.return_value = scripts
        elif column == "id":
            result.scalar.return_value = object() if has_unlabeled else None
        elif column == "chunk_text":
            result.all.return_value = chunks
        else:  # pragma: no cover — no other statements expected
            raise AssertionError(f"unexpected statement selecting {column!r}")
        return result

    db.execute = MagicMock(side_effect=_execute)
    return db


def _clear_kb_script_caches(tenant_id) -> None:
    from backend.search.service import (
        _TENANT_KB_SCRIPT_CACHE,
        _TENANT_KB_SCRIPTS_CACHE,
    )

    _TENANT_KB_SCRIPT_CACHE.pop(str(tenant_id), None)
    _TENANT_KB_SCRIPTS_CACHE.pop(str(tenant_id), None)


class TestDetectTenantKbScript:
    """async_detect_tenant_kb_script() reads Document.script stored at parse time."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("script_rows", "expected"),
        [
            pytest.param(
                [("cyrillic", 3)], "cyrillic", id="returns_the_only_script_present"
            ),
            pytest.param(
                [("greek", 2)],
                "greek",
                id="returns_a_script_outside_the_two_legacy_buckets",
                # Regression: every non-latin/cyrillic KB used to collapse to None.
            ),
            pytest.param(
                [("latin", 3), ("cyrillic", 1)],
                "latin",
                id="dominant_wins_for_mixed_kb",
            ),
            pytest.param([], None, id="returns_none_for_empty_kb"),
        ],
    )
    async def test_returns_the_dominant_or_only_script(self, script_rows, expected):
        import uuid
        from backend.search.service import async_detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        _clear_kb_script_caches(tenant_id)
        # chunk_rows=[] so the empty-KB case does not fall through to chunk
        # sampling and pick up a stray script.
        mock_db = _make_async_db(script_rows=script_rows, chunk_rows=[])

        assert await async_detect_tenant_kb_script(tenant_id, mock_db) == expected

    @pytest.mark.asyncio
    async def test_falls_back_to_chunk_sampling_when_no_language_set(self):
        """Legacy KBs (Document.script all NULL) fall back to chunk sampling."""
        import uuid
        from backend.search.service import async_detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        _clear_kb_script_caches(tenant_id)

        # Stored-script path returns no rows → chunk sampling kicks in.
        mock_db = _make_async_db(
            script_rows=[],
            chunk_rows=[
                ("Сайт не открывается после подключения к CDN.",),
                ("Проверьте NS-пропагацию.",),
            ],
        )

        assert await async_detect_tenant_kb_script(tenant_id, mock_db) == "cyrillic"

    @pytest.mark.asyncio
    async def test_partial_labeling_augments_labeled_with_chunk_sample(self):
        """Partial labeling must not blind us to unlabeled legacy documents.

        Regression: a single newly labeled document on top of a hundred
        legacy ones (all with script=NULL) used to misclassify the KB as
        single-script, making the other half unreachable for cross-lingual
        rewrite.
        """
        import uuid
        from backend.search.service import async_detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        _clear_kb_script_caches(tenant_id)

        mock_db = _make_async_db(
            # One labeled latin-script document.
            script_rows=[("latin", 1)],
            # _async_tenant_has_unlabeled_documents → at least one NULL row.
            has_unlabeled=True,
            # Chunk sampling sees the legacy cyrillic-script content.
            chunk_rows=[
                ("Сайт не открывается после подключения к CDN.",),
                ("Проверьте NS-пропагацию.",),
                ("A-запись домена должна указывать на IP-адреса CDN.",),
            ],
        )

        # The sampled majority dominates → cyrillic, not latin.
        assert await async_detect_tenant_kb_script(tenant_id, mock_db) == "cyrillic"

    @pytest.mark.asyncio
    async def test_full_labeling_skips_chunk_sampling(self):
        """When every document has a script set, sampling is not invoked."""
        import uuid
        from backend.search.service import async_detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        _clear_kb_script_caches(tenant_id)

        # No unlabeled rows → sampling must be skipped.
        mock_db = _make_async_db(script_rows=[("latin", 2)], has_unlabeled=False)

        assert await async_detect_tenant_kb_script(tenant_id, mock_db) == "latin"
        # chunk_text is the entry point for chunk sampling; assert it never ran.
        assert "chunk_text" not in mock_db.executed_columns

    @pytest.mark.asyncio
    async def test_uses_cache_on_second_call(self):
        import uuid
        from backend.search.service import async_detect_tenant_kb_script

        tenant_id = uuid.uuid4()
        _clear_kb_script_caches(tenant_id)
        mock_db = _make_async_db(script_rows=[("cyrillic", 1)])

        await async_detect_tenant_kb_script(tenant_id, mock_db)
        calls_after_first = mock_db.execute.call_count
        await async_detect_tenant_kb_script(tenant_id, mock_db)

        # Second call must hit the cache and issue no further queries.
        assert mock_db.execute.call_count == calls_after_first


class TestDetectTenantKbScripts:
    """async_detect_tenant_kb_scripts() returns the full set of buckets in the KB."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("script_rows", "expected"),
        [
            pytest.param(
                [("latin", 2), ("cyrillic", 1)],
                frozenset({"cyrillic", "latin"}),
                id="mixed_kb_returns_both_buckets",
                # A two-script KB returns both, so cross-lingual rewrite reaches both.
            ),
            pytest.param(
                [("latin", 3), ("greek", 2), ("arabic", 1)],
                frozenset({"latin", "greek", "arabic"}),
                id="returns_every_script_present_beyond_two",
                # Buckets outside the two legacy ones survive instead of being dropped.
            ),
            pytest.param(
                [("latin", 400), ("arabic", 1)],
                frozenset({"latin"}),
                id="drops_a_script_carried_by_a_stray_document",
                # Each returned script costs an LLM call per turn; strays must not.
            ),
            pytest.param(
                [("latin", 400), ("cyrillic", 30)],
                frozenset({"latin", "cyrillic"}),
                id="keeps_a_genuine_minority_script_section",
                # A real minority-language section still earns a cross-lingual rewrite.
            ),
            pytest.param([], frozenset(), id="empty_kb_returns_empty_set"),
        ],
    )
    async def test_returns_the_kb_script_set(self, script_rows, expected):
        import uuid
        from backend.search.service import async_detect_tenant_kb_scripts

        tenant_id = uuid.uuid4()
        _clear_kb_script_caches(tenant_id)
        mock_db = _make_async_db(script_rows=script_rows, chunk_rows=[])

        assert await async_detect_tenant_kb_scripts(tenant_id, mock_db) == expected


# ---------------------------------------------------------------------------
# async_semantic_query_rewrite_for_kb
# ---------------------------------------------------------------------------

class TestSemanticQueryRewriteForKb:
    """async_semantic_query_rewrite_for_kb() produces a KB-language rewrite."""

    @pytest.mark.asyncio
    async def test_returns_a_rewrite_targeting_the_kb_script(self):
        from backend.search.service import async_semantic_query_rewrite_for_kb

        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            mock_retry.return_value = _make_openai_response(
                "диагностика подключения CDN NS-пропагация A-запись SSL"
            )
            mock_client.return_value = MagicMock()

            result = await async_semantic_query_rewrite_for_kb(
                "The site doesn't open after connecting — what should I check?",
                kb_script="cyrillic",
                api_key="sk-test",
            )

        assert result == "диагностика подключения CDN NS-пропагация A-запись SSL"

    @pytest.mark.asyncio
    async def test_rewrites_for_a_script_outside_the_two_legacy_buckets(self):
        """Regression: any KB script but latin/cyrillic used to return None."""
        from backend.search.service import async_semantic_query_rewrite_for_kb

        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            mock_retry.return_value = _make_openai_response("επαναφορά κωδικού")
            client = MagicMock()
            mock_client.return_value = client

            result = await async_semantic_query_rewrite_for_kb(
                "How do I reset my password?",
                kb_script="greek",
                api_key="sk-test",
            )

        assert result == "επαναφορά κωδικού"
        # The retry wrapper is mocked, so run its payload to capture the prompt.
        mock_retry.call_args.args[1]()
        create_kwargs = client.chat.completions.create.call_args.kwargs
        assert "greek" in create_kwargs["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_returns_none_for_letterless_script(self):
        from backend.search.service import async_semantic_query_rewrite_for_kb

        result = await async_semantic_query_rewrite_for_kb(
            "Some question",
            kb_script="other",
            api_key="sk-test",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_failure(self):
        from backend.search.service import async_semantic_query_rewrite_for_kb

        client_patch, retry_patch = _patch_rewrite_layer()
        with client_patch as mock_client, retry_patch as mock_retry:
            mock_retry.side_effect = RuntimeError("LLM error")
            mock_client.return_value = MagicMock()

            result = await async_semantic_query_rewrite_for_kb(
                "The site doesn't open after connecting",
                kb_script="cyrillic",
                api_key="sk-test",
            )

        assert result is None


# ---------------------------------------------------------------------------
# _bm25_queries_for_script — non-EN now includes original query
# ---------------------------------------------------------------------------

_OMIT_KB_SCRIPT = object()


class TestBm25QueriesForScriptNonEn:
    """Non-EN queries: order depends on whether KB script matches query script."""

    @pytest.mark.parametrize(
        ("query", "en_rewrite", "query_script", "kb_script", "expect_rewrite_first"),
        [
            pytest.param(
                "сайт не открывается после подключения",
                "site connectivity troubleshooting",
                "cyrillic",
                "cyrillic",
                False,
                id="same_script_kb_puts_original_first",
                # Cyrillic query + Cyrillic KB → original first for same-language BM25.
            ),
            pytest.param(
                "сайт не открывается",
                "CDN site connectivity troubleshooting",
                "cyrillic",
                "latin",
                True,
                id="cross_script_kb_puts_en_rewrite_first",
                # Cyrillic query + Latin KB → EN rewrite first for asymmetric BM25.
            ),
            pytest.param(
                "сайт не открывается",
                "CDN site connectivity troubleshooting",
                "cyrillic",
                _OMIT_KB_SCRIPT,
                True,
                id="unknown_kb_script_puts_en_rewrite_first",
                # No kb_script → EN rewrite first (safe default for unknown KB language).
            ),
        ],
    )
    def test_orders_variants_by_script_match(
        self, query, en_rewrite, query_script, kb_script, expect_rewrite_first
    ):
        from backend.search.service import _bm25_queries_for_script

        variants = [query, en_rewrite]
        args = (query, variants, query_script)
        kwargs = {} if kb_script is _OMIT_KB_SCRIPT else {"kb_script": kb_script}
        result = _bm25_queries_for_script(*args, **kwargs)

        expected_first = en_rewrite if expect_rewrite_first else query
        assert result[0] == expected_first
        assert query in result and en_rewrite in result

    def test_en_query_unchanged(self):
        from backend.search.service import _bm25_queries_for_script

        query = "site does not open after connecting"
        result = _bm25_queries_for_script(query, [query], "latin")

        assert result == [query]
