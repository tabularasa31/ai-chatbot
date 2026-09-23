"""Unit tests for ThoughtStreamFilter — streaming suppression of <thought> blocks."""

from __future__ import annotations

import pytest

from backend.chat.streaming import (
    ThoughtStreamFilter,
)


def _collect(chunks: list[str]) -> str:
    """Feed a list of chunks into ThoughtStreamFilter and return emitted text."""
    received: list[str] = []
    f = ThoughtStreamFilter(received.append)
    for chunk in chunks:
        f.feed(chunk)
    f.flush_end()
    return "".join(received)


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        pytest.param(["Hello, world!"], "Hello, world!", id="plain_text_pass_through"),
        pytest.param(["Hello", ", ", "world!"], "Hello, world!", id="plain_text_multi_chunk_pass_through"),
        pytest.param([], "", id="empty_input"),
        pytest.param([""], "", id="single_empty_chunk"),
        pytest.param(["<thought>reasoning</thought>answer"], "answer", id="thought_tag_whole_chunk"),
        pytest.param(["<thought>cot</thought>Real answer"], "Real answer", id="thought_tag_at_start"),
        pytest.param(["answer<thought>trailing cot</thought>"], "answer", id="thought_tag_at_end"),
        pytest.param(["start<thought>hidden</thought>end"], "startend", id="thought_tag_in_middle"),
        pytest.param(["<thought>only thoughts</thought>"], "", id="thought_tag_only_content"),
        pytest.param(["<", "thought>hidden</thought>answer"], "answer", id="open_tag_split_after_lt"),
        pytest.param(["<tho", "ught>hidden</thought>answer"], "answer", id="open_tag_split_in_middle"),
        pytest.param(
            list("<thought>hidden</thought>answer"),
            "answer",
            id="open_tag_split_one_char_at_a_time",
        ),
        pytest.param(["<thought>hidden</", "thought>answer"], "answer", id="close_tag_split"),
        pytest.param(["<thought>hidden</thoug", "ht>answer"], "answer", id="close_tag_split_one_char"),
        pytest.param(["text<tho", "ught>hidden</tho", "ught>more"], "textmore", id="both_tags_split"),
        pytest.param(
            ["<thought>first</thought>between<thought>second</thought>end"],
            "betweenend",
            id="multiple_thought_blocks",
        ),
        pytest.param(
            ["<thought>first</thought>", "between", "<thought>second</thought>", "end"],
            "betweenend",
            id="multiple_thought_blocks_split",
        ),
        pytest.param(
            ["<thought>a</thought><thought>b</thought>answer"], "answer", id="adjacent_thought_blocks"
        ),
        pytest.param(["<thought>no close tag"], "", id="unclosed_thought_discarded"),
        pytest.param(["answer<thought>no close"], "answer", id="unclosed_thought_after_answer"),
        pytest.param(
            ["answer<tho"],
            "answer<tho",
            id="unclosed_thought_split_open_tag",
            # "<tho" is held in buffer during streaming in case the next chunk
            # completes "<thought>", but at flush_end we know the tag never
            # arrived, so the buffered text is emitted.
        ),
        pytest.param(
            ["<thunder>text"],
            "<thunder>text",
            id="incomplete_open_tag_not_a_thought",
            # "<thunder>" is NOT "<thought>", so "<th" gets emitted once "u" mismatches.
        ),
        pytest.param(["3 < 5 is true"], "3 < 5 is true", id="less_than_not_followed_by_thought"),
        pytest.param(["x<", "yz"], "x<yz", id="less_than_at_end_of_chunk_then_non_thought"),
        pytest.param(
            ["<thought>  spaces  </thought>answer"], "answer", id="thought_tag_with_whitespace_content"
        ),
        pytest.param(["a < b > c"], "a < b > c", id="nested_angle_brackets_outside_thought"),
        pytest.param(
            ["prefix", "<thought>hidden</thought>", "suffix"],
            "prefixsuffix",
            id="no_double_emit_on_boundary",
        ),
        pytest.param(
            [f"<thought>{'x' * 100_000}</thought>answer"],
            "answer",
            id="large_thought_block_does_not_buffer_unboundedly",
        ),
        pytest.param(
            ["<thought>" + "x" * 50_000, "y" * 50_000 + "</thought>end"],
            "end",
            id="large_thought_block_split",
        ),
    ],
)
def test_thought_filter_pass_through_and_stripping(chunks: list[str], expected: str) -> None:
    assert _collect(chunks) == expected


def test_flush_end_idempotent() -> None:
    """Calling flush_end twice does not double-emit."""
    received: list[str] = []
    f = ThoughtStreamFilter(received.append)
    f.feed("hello")
    f.flush_end()
    f.flush_end()
    assert "".join(received) == "hello"


@pytest.mark.parametrize(
    ("chunks", "expected_text"),
    [
        pytest.param(
            ["intro <thought>cot</thought>final"],
            "intro final",
            id="phase_callback_fires_on_thought_boundaries",
        ),
        pytest.param(
            ["<tho", "ught>hidden</tho", "ught>visible"],
            "visible",
            id="phase_callback_split_across_chunks",
        ),
    ],
)
def test_phase_callback_fires_once_per_transition(chunks: list[str], expected_text: str) -> None:
    """on_phase_change emits 'reasoning' on <thought>, 'writing' on </thought>,
    exactly once per transition even when the tag is split across feed() calls."""
    phases: list[str] = []
    received: list[str] = []
    f = ThoughtStreamFilter(received.append, on_phase_change=phases.append)
    for chunk in chunks:
        f.feed(chunk)
    f.flush_end()
    assert phases == ["reasoning", "writing"]
    assert "".join(received) == expected_text


def test_phase_callback_failure_does_not_break_stream() -> None:
    """An exception in on_phase_change must not prevent emit() from running."""
    received: list[str] = []

    def boom(_: str) -> None:
        raise RuntimeError("phase callback exploded")

    f = ThoughtStreamFilter(received.append, on_phase_change=boom)
    f.feed("a<thought>x</thought>b")
    f.flush_end()
    assert "".join(received) == "ab"
