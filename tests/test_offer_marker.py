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


def test_strip_empty_string():
    cleaned, offered = _strip_and_detect_offer_marker("")
    assert offered is False
    assert cleaned == ""


def test_stream_filter_marker_in_single_chunk():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed("Offer text here. " + OFFER_MARKER)
    f.flush_end()
    assert f.detected is True
    assert "".join(out) == "Offer text here. "


def test_stream_filter_marker_split_across_chunks():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    # Split the marker right down the middle to verify split-boundary buffering.
    half = len(OFFER_MARKER) // 2
    f.feed("Hello world. " + OFFER_MARKER[:half])
    f.feed(OFFER_MARKER[half:])
    f.flush_end()
    assert f.detected is True
    emitted = "".join(out)
    assert OFFER_MARKER not in emitted
    assert emitted == "Hello world. "


def test_stream_filter_no_marker_passes_through_unchanged():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed("Here is a plain ")
    f.feed("answer with no offer.")
    f.flush_end()
    assert f.detected is False
    assert "".join(out) == "Here is a plain answer with no offer."


def test_stream_filter_text_then_marker_then_more_text():
    # Defensive: even if the LLM puts text after the marker (against the
    # prompt), the filter must still strip the marker and not lose either
    # the leading or trailing real text.
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed("Before. " + OFFER_MARKER + "After.")
    f.flush_end()
    assert f.detected is True
    assert "".join(out) == "Before. After."


def test_stream_filter_marker_only():
    out: list[str] = []
    f = OfferMarkerStreamFilter(out.append)
    f.feed(OFFER_MARKER)
    f.flush_end()
    assert f.detected is True
    assert "".join(out) == ""
