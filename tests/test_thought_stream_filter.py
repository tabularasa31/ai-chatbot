"""Unit tests for ThoughtStreamFilter — streaming suppression of <thought> blocks."""

from __future__ import annotations

import pytest

from backend.chat.handlers.rag import ThoughtStreamFilter


def _collect(chunks: list[str]) -> str:
    """Feed a list of chunks into ThoughtStreamFilter and return emitted text."""
    received: list[str] = []
    f = ThoughtStreamFilter(received.append)
    for chunk in chunks:
        f.feed(chunk)
    f.flush_end()
    return "".join(received)


# ---------------------------------------------------------------------------
# No thought tags — pass-through behaviour
# ---------------------------------------------------------------------------


def test_plain_text_pass_through() -> None:
    assert _collect(["Hello, world!"]) == "Hello, world!"


def test_plain_text_multi_chunk_pass_through() -> None:
    assert _collect(["Hello", ", ", "world!"]) == "Hello, world!"


def test_empty_input() -> None:
    assert _collect([]) == ""


def test_single_empty_chunk() -> None:
    assert _collect([""]) == ""


# ---------------------------------------------------------------------------
# Whole tag arrives in a single chunk
# ---------------------------------------------------------------------------


def test_thought_tag_whole_chunk() -> None:
    assert _collect(["<thought>reasoning</thought>answer"]) == "answer"


def test_thought_tag_at_start() -> None:
    assert _collect(["<thought>cot</thought>Real answer"]) == "Real answer"


def test_thought_tag_at_end() -> None:
    assert _collect(["answer<thought>trailing cot</thought>"]) == "answer"


def test_thought_tag_in_middle() -> None:
    result = _collect(["start<thought>hidden</thought>end"])
    assert result == "startend"


def test_thought_tag_only_content() -> None:
    assert _collect(["<thought>only thoughts</thought>"]) == ""


# ---------------------------------------------------------------------------
# Tag split across chunk boundaries
# ---------------------------------------------------------------------------


def test_open_tag_split_after_lt() -> None:
    """<thought> split after the opening '<'."""
    result = _collect(["<", "thought>hidden</thought>answer"])
    assert result == "answer"


def test_open_tag_split_in_middle() -> None:
    """<thought> split in the middle of the tag text."""
    result = _collect(["<tho", "ught>hidden</thought>answer"])
    assert result == "answer"


def test_open_tag_split_one_char_at_a_time() -> None:
    """<thought> delivered one character at a time."""
    tag = "<thought>hidden</thought>answer"
    result = _collect(list(tag))
    assert result == "answer"


def test_close_tag_split() -> None:
    """</thought> split across chunks."""
    result = _collect(["<thought>hidden</", "thought>answer"])
    assert result == "answer"


def test_close_tag_split_one_char() -> None:
    result = _collect(["<thought>hidden</thoug", "ht>answer"])
    assert result == "answer"


def test_both_tags_split() -> None:
    result = _collect(["text<tho", "ught>hidden</tho", "ught>more"])
    assert result == "textmore"


# ---------------------------------------------------------------------------
# Multiple thought blocks
# ---------------------------------------------------------------------------


def test_multiple_thought_blocks() -> None:
    result = _collect(["<thought>first</thought>between<thought>second</thought>end"])
    assert result == "betweenend"


def test_multiple_thought_blocks_split() -> None:
    result = _collect([
        "<thought>first</thought>",
        "between",
        "<thought>second</thought>",
        "end",
    ])
    assert result == "betweenend"


def test_adjacent_thought_blocks() -> None:
    result = _collect(["<thought>a</thought><thought>b</thought>answer"])
    assert result == "answer"


# ---------------------------------------------------------------------------
# Unclosed tag at end of stream
# ---------------------------------------------------------------------------


def test_unclosed_thought_discarded() -> None:
    """An unclosed <thought> block at end of stream is silently discarded."""
    result = _collect(["<thought>no close tag"])
    assert result == ""


def test_unclosed_thought_after_answer() -> None:
    result = _collect(["answer<thought>no close"])
    assert result == "answer"


def test_unclosed_thought_split_open_tag() -> None:
    """Partial <thought> prefix at end of stream is emitted — not a confirmed thought block."""
    result = _collect(["answer<tho"])
    # "<tho" is held in buffer during streaming in case next chunk completes "<thought>",
    # but at flush_end we know the tag never arrived, so the buffered text is emitted.
    assert result == "answer<tho"


def test_incomplete_open_tag_not_a_thought() -> None:
    """'<thunder>' is NOT <thought>, so '<th' prefix gets emitted once 'u' mismatches."""
    result = _collect(["<thunder>text"])
    assert result == "<thunder>text"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_less_than_not_followed_by_thought() -> None:
    """A bare '<' not followed by 'thought' is eventually emitted."""
    result = _collect(["3 < 5 is true"])
    assert result == "3 < 5 is true"


def test_less_than_at_end_of_chunk_then_non_thought() -> None:
    """'<' at end of chunk, next chunk starts with non-thought char."""
    result = _collect(["x<", "yz"])
    assert result == "x<yz"


def test_thought_tag_with_whitespace_content() -> None:
    result = _collect(["<thought>  spaces  </thought>answer"])
    assert result == "answer"


def test_nested_angle_brackets_outside_thought() -> None:
    result = _collect(["a < b > c"])
    assert result == "a < b > c"


def test_no_double_emit_on_boundary() -> None:
    """Text before thought tag is emitted exactly once."""
    received: list[str] = []
    f = ThoughtStreamFilter(received.append)
    f.feed("prefix")
    f.feed("<thought>hidden</thought>")
    f.feed("suffix")
    f.flush_end()
    assert "".join(received) == "prefixsuffix"


def test_flush_end_idempotent() -> None:
    """Calling flush_end twice does not double-emit."""
    received: list[str] = []
    f = ThoughtStreamFilter(received.append)
    f.feed("hello")
    f.flush_end()
    f.flush_end()
    assert "".join(received) == "hello"


def test_large_thought_block_does_not_buffer_unboundedly() -> None:
    """Large thought content is not held in memory after processing."""
    large = "x" * 100_000
    result = _collect([f"<thought>{large}</thought>answer"])
    assert result == "answer"


def test_large_thought_block_split() -> None:
    large_chunk = "<thought>" + "x" * 50_000
    result = _collect([large_chunk, "y" * 50_000 + "</thought>end"])
    assert result == "end"
