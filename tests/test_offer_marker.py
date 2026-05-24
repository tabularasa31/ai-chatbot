"""Unit tests for the language-agnostic ticket-offer marker.

Covers ``_strip_and_detect_offer_marker`` (post-generation parse) and
``OfferMarkerStreamFilter`` (streaming-SSE filter), which together replace
the natural-language regex on the live request path.
"""

from backend.chat.handlers.rag import (
    OFFER_MARKER,
    OfferMarkerStreamFilter,
    _strip_and_detect_offer_marker,
)


def test_strip_marker_present_at_end():
    text = "Я не нашёл это в документации. Хотите тикет?" + OFFER_MARKER
    cleaned, offered = _strip_and_detect_offer_marker(text)
    assert offered is True
    assert OFFER_MARKER not in cleaned
    assert cleaned == "Я не нашёл это в документации. Хотите тикет?"


def test_strip_marker_with_trailing_whitespace():
    text = "Offer text here. " + OFFER_MARKER + "\n"
    cleaned, offered = _strip_and_detect_offer_marker(text)
    assert offered is True
    assert cleaned == "Offer text here."


def test_strip_no_marker():
    text = "Это полноценный ответ без оффера."
    cleaned, offered = _strip_and_detect_offer_marker(text)
    assert offered is False
    assert cleaned == text


def test_strip_marker_in_middle_is_not_detected():
    # The prompt contract is "marker is the very last token". A literal that
    # appears mid-text (LLM echoes a user question, docs quote the token, …)
    # must NOT arm pre_confirm and must NOT have its surrounding content
    # rewritten by the detector. Defensive UX stripping for the streaming
    # path is the OfferMarkerStreamFilter's job, not this one.
    text = "Here is what the marker " + OFFER_MARKER + " looks like in docs."
    cleaned, offered = _strip_and_detect_offer_marker(text)
    assert offered is False
    assert cleaned == text


def test_strip_empty_string():
    cleaned, offered = _strip_and_detect_offer_marker("")
    assert offered is False
    assert cleaned == ""


def test_stream_filter_marker_in_single_chunk():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed("Offer text here. " + OFFER_MARKER)
    f.flush_end()
    assert "".join(out) == "Offer text here. "


def test_stream_filter_marker_split_across_chunks():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    # Split the marker right down the middle to verify split-boundary buffering.
    half = len(OFFER_MARKER) // 2
    f.feed("Hello world. " + OFFER_MARKER[:half])
    f.feed(OFFER_MARKER[half:])
    f.flush_end()
    emitted = "".join(out)
    assert OFFER_MARKER not in emitted
    assert emitted == "Hello world. "


def test_stream_filter_no_marker_passes_through_unchanged():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed("Here is a plain ")
    f.feed("answer with no offer.")
    f.flush_end()
    assert "".join(out) == "Here is a plain answer with no offer."


def test_stream_filter_text_then_marker_then_more_text():
    # Defensive UX: even if the LLM puts text after the marker (against the
    # prompt contract), the filter still strips the marker from the visible
    # stream so the user never sees the literal. Detection (= whether to arm
    # pre_confirm) is terminal-only and lives in _strip_and_detect_offer_marker.
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed("Before. " + OFFER_MARKER + "After.")
    f.flush_end()
    assert "".join(out) == "Before. After."


def test_stream_filter_marker_only():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed(OFFER_MARKER)
    f.flush_end()
    assert "".join(out) == ""
