"""Unit tests for the language-agnostic ticket-offer marker.

Covers ``_strip_and_detect_offer_marker`` (post-generation parse) and
``OfferMarkerStreamFilter`` (streaming-SSE filter), which together replace
the natural-language regex on the live request path.
"""

import pytest

from backend.chat.handlers.rag import (
    OFFER_MARKER,
    OfferMarkerStreamFilter,
    _scrub_offer_marker_literal,
    _strip_and_detect_offer_marker,
)


@pytest.mark.parametrize(
    ("text", "expected_offered", "expected_cleaned"),
    [
        pytest.param(
            "Я не нашёл это в документации. Хотите тикет?" + OFFER_MARKER,
            True,
            "Я не нашёл это в документации. Хотите тикет?",
            id="marker_present_at_end",
        ),
        pytest.param(
            "Offer text here. " + OFFER_MARKER + "\n",
            True,
            "Offer text here.",
            id="marker_with_trailing_whitespace",
        ),
        pytest.param(
            "Это полноценный ответ без оффера.",
            False,
            "Это полноценный ответ без оффера.",
            id="no_marker",
        ),
        pytest.param(
            "Here is what the marker " + OFFER_MARKER + " looks like in docs.",
            False,
            None,
            # The prompt contract is "marker is the very last token". A literal
            # that appears mid-text (LLM echoes a user question, docs quote the
            # token, …) must NOT arm pre_confirm and must NOT be rewritten here —
            # defensive UX stripping for the streaming path is
            # OfferMarkerStreamFilter's job, not this one.
            id="marker_in_middle_is_not_detected",
        ),
        pytest.param("", False, "", id="empty_string"),
        pytest.param(
            "Want me to open a support ticket?" + OFFER_MARKER + ".",
            True,
            "Want me to open a support ticket?",
            # Common LLM tic: appends a stray period after the sentinel.
            id="marker_with_trailing_period",
        ),
        pytest.param(
            "Хотите тикет?" + OFFER_MARKER + '".',
            True,
            None,
            id="marker_with_trailing_quote_and_punct",
        ),
        pytest.param(
            "Want a ticket?" + OFFER_MARKER + " By the way, here are other tips.",
            False,
            None,
            # If real natural-language text follows the marker, the LLM violated
            # the "very last token" contract — don't treat it as terminal.
            id="marker_followed_by_substantive_text_not_detected",
        ),
    ],
)
def test_strip_and_detect_offer_marker(text, expected_offered, expected_cleaned):
    cleaned, offered = _strip_and_detect_offer_marker(text)
    assert offered is expected_offered
    if expected_cleaned is not None:
        assert cleaned == expected_cleaned
    elif expected_offered:
        assert OFFER_MARKER not in cleaned
    else:
        assert cleaned == text


def _collect(feeds: list[str]) -> str:
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    for chunk in feeds:
        f.feed(chunk)
    f.flush_end()
    return "".join(out)


@pytest.mark.parametrize(
    ("feeds", "expected"),
    [
        pytest.param(
            ["Offer text here. " + OFFER_MARKER], "Offer text here. ", id="marker_in_single_chunk"
        ),
        pytest.param(
            # Split the marker right down the middle to verify split-boundary buffering.
            ["Hello world. " + OFFER_MARKER[: len(OFFER_MARKER) // 2], OFFER_MARKER[len(OFFER_MARKER) // 2 :]],
            "Hello world. ",
            id="marker_split_across_chunks",
        ),
        pytest.param(
            ["Here is a plain ", "answer with no offer."],
            "Here is a plain answer with no offer.",
            id="no_marker_passes_through_unchanged",
        ),
        pytest.param(
            ["Before. " + OFFER_MARKER + "After."],
            "Before. After.",
            # Defensive UX: even if the LLM puts text after the marker (against
            # the prompt contract), the filter still strips the marker from the
            # visible stream. Whether to arm pre_confirm is terminal-only and
            # lives in _strip_and_detect_offer_marker, not here.
            id="text_then_marker_then_more_text",
        ),
        pytest.param([OFFER_MARKER], "", id="marker_only"),
        pytest.param(
            # Stream ends with a partial marker prefix buffered (max_completion_tokens
            # hit, client disconnect, OpenAI 5xx mid-stream). The rest of the marker
            # never arrives — emitting the partial literal would leak garbage to the UI.
            ["Want a ticket? " + OFFER_MARKER[: len(OFFER_MARKER) // 2]],
            "Want a ticket? ",
            id="truncated_stream_drops_partial_marker",
        ),
    ],
)
def test_offer_marker_stream_filter(feeds, expected):
    emitted = _collect(feeds)
    assert emitted == expected
    assert OFFER_MARKER not in emitted
    assert "<" not in emitted


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "Before " + OFFER_MARKER + " middle " + OFFER_MARKER + " end",
            "Before  middle  end",
            id="removes_mid_text_literal",
        ),
    ],
)
def test_scrub_offer_marker_literal_removes(text, expected):
    scrubbed = _scrub_offer_marker_literal(text)
    assert OFFER_MARKER not in scrubbed
    assert scrubbed == expected


def test_scrub_offer_marker_literal_noop_when_no_marker():
    text = "Plain answer."
    assert _scrub_offer_marker_literal(text) is text
