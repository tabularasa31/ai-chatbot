"""Unit tests for backend/evals/.

These tests run in regular CI (no real Anthropic / OpenAI calls) by
injecting fake chat clients and stubbing the judge. The end-to-end
flow against a real backend is exercised manually via
``python -m backend.evals run``.
"""

from __future__ import annotations


import pytest
import yaml

from backend.evals.client import ChatResponse, _aggregate_events, _parse_sse_data
from backend.evals.dataset import Dataset, GoldenCase, load_dataset
from backend.evals.judge import _parse_judge_response, JudgeResult
from backend.evals.metrics import (
    check_language,
    check_must_contain,
    check_must_not_contain,
)
from backend.evals.report import (
    CaseResult,
    RunReport,
    render_markdown,
)
from backend.evals.runner import RunnerConfig, run


class _FakeChat:
    """Stand-in for ChatClient that returns canned responses keyed by input."""

    def __init__(self, replies: dict[str, str], bot_public_id: str = "ch_fake") -> None:
        self._replies = replies
        self.bot_public_id = bot_public_id
        self.calls: list[str] = []

    def ask(self, question: str, *, session_id: str | None = None) -> ChatResponse:
        self.calls.append(question)
        text = self._replies.get(question, "")
        return ChatResponse(text=text, sources=[], chat_ended=False, latency_ms=42)


# ─── dataset ────────────────────────────────────────────────────────────────


def test_load_chat9_basic_dataset() -> None:
    """The committed golden dataset must round-trip through the schema."""
    ds = load_dataset("tests/eval/datasets/chat9_basic.yaml")
    assert ds.name == "chat9_basic"
    assert len(ds.cases) >= 12
    cats = {c.category for c in ds.cases}
    assert cats == {"happy_path", "rag", "guards", "multilingual", "golden_scenario"}
    langs = {c.lang for c in ds.cases}
    assert "ru" in langs and "en" in langs


def test_dataset_rejects_duplicate_case_ids(tmp_path) -> None:
    p = tmp_path / "dup.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "dup",
                "cases": [
                    {"id": "x", "category": "happy_path", "input": "a"},
                    {"id": "x", "category": "happy_path", "input": "b"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="duplicate case id"):
        load_dataset(p)


def test_dataset_rejects_unknown_category(tmp_path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "bad",
                "cases": [{"id": "x", "category": "made_up", "input": "a"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_dataset(p)


# ─── deterministic metrics ──────────────────────────────────────────────────


def _case(**kwargs) -> GoldenCase:
    base = {"id": "t1", "category": "happy_path", "lang": "en", "input": "Q"}
    base.update(kwargs)
    return GoldenCase(**base)


def test_must_contain_passes_with_substring() -> None:
    c = _case(must_contain=["Free", "early"])
    assert check_must_contain(c, "Chat9 is free during early access").passed


def test_must_contain_fails_with_missing() -> None:
    c = _case(must_contain=["paid"])
    r = check_must_contain(c, "Chat9 is free")
    assert not r.passed and "missing" in r.detail


def test_must_not_contain_blocks_banned_phrase() -> None:
    c = _case(must_not_contain=["claude"])
    r = check_must_not_contain(c, "We use Claude under the hood.")
    assert not r.passed and "claude" in r.detail.lower()


def test_language_check_skipped_for_any() -> None:
    c = _case(lang="any")
    assert check_language(c, "anything").passed


def test_language_check_detects_russian() -> None:
    c = _case(lang="ru")
    r = check_language(
        c, "Chat9 — это платформа для встроенных AI-ботов на сайте поддержки клиентов."
    )
    assert r.passed, r.detail


def test_language_check_flags_mismatch() -> None:
    c = _case(lang="en")
    r = check_language(
        c, "Это платформа для встроенных AI-ботов на сайте поддержки клиентов."
    )
    assert not r.passed and "expected=en" in r.detail


# ─── judge response parser ──────────────────────────────────────────────────


def test_judge_parser_strict_json() -> None:
    raw = '{"score": 0.85, "rationale": "good answer"}'
    r = _parse_judge_response(raw, model="m")
    assert r.score == 0.85 and "good" in r.rationale and r.model == "m"


def test_judge_parser_handles_prose_around_json() -> None:
    raw = 'Sure! Here is my evaluation:\n\n{"score": 0.4, "rationale": "weak"}\n\nThanks.'
    r = _parse_judge_response(raw, model="m")
    assert r.score == 0.4 and r.rationale == "weak"


def test_judge_parser_clamps_out_of_range_scores() -> None:
    raw = '{"score": 1.7, "rationale": "x"}'
    assert _parse_judge_response(raw, "m").score == 1.0
    raw = '{"score": -0.3, "rationale": "x"}'
    assert _parse_judge_response(raw, "m").score == 0.0


def test_judge_parser_falls_back_on_invalid_json() -> None:
    r = _parse_judge_response("not json at all", "m")
    assert r.score == 0.0


# ─── SSE client helpers ─────────────────────────────────────────────────────


def test_sse_aggregator_concatenates_chunks_and_uses_done_text() -> None:
    events = [
        {"type": "chunk", "text": "Hello "},
        {"type": "chunk", "text": "world."},
        {"type": "done", "text": "Hello world.", "chat_ended": False, "sources": [{"title": "x", "url": "y"}]},
    ]
    r = _aggregate_events(events, latency_ms=10)
    assert r.text == "Hello world." and r.sources and r.error is None


def test_sse_aggregator_surfaces_error_event() -> None:
    events = [
        {"type": "chunk", "text": "partial"},
        {"type": "error", "code": 503, "message": "OpenAI service unavailable"},
    ]
    r = _aggregate_events(events, latency_ms=5)
    assert r.error is not None and r.error["code"] == 503
    # chunks before the error are still preserved on .text
    assert r.text == "partial"


def test_sse_data_parser_skips_non_data_lines() -> None:
    assert _parse_sse_data("event: ping") is None
    assert _parse_sse_data("data: ") is None
    assert _parse_sse_data('data: {"type":"chunk","text":"x"}') == {"type": "chunk", "text": "x"}


# ─── runner end-to-end (mocked chat + judge) ────────────────────────────────


def test_runner_runs_dataset_with_mocked_chat_and_no_judge() -> None:
    cases = [
        GoldenCase(
            id="hp1",
            category="happy_path",
            lang="en",
            input="Is Chat9 free?",
            must_contain=["free"],
        ),
        GoldenCase(
            id="hp2",
            category="happy_path",
            lang="en",
            input="What model does it use?",
            must_contain=["gpt"],
        ),
    ]
    ds = Dataset(name="t", cases=cases)
    chat = _FakeChat(
        {
            "Is Chat9 free?": "Yes — Chat9 is free during early access.",
            "What model does it use?": "We use gpt-5-mini for chat answers.",
        }
    )
    report = run(RunnerConfig(dataset=ds, tag="unit", chat=chat, judge=None))
    assert report.total == 2 and report.passed_count == 2
    assert chat.calls == ["Is Chat9 free?", "What model does it use?"]


def test_runner_records_chat_failure_per_case() -> None:
    cases = [
        GoldenCase(id="x", category="happy_path", lang="en", input="anything"),
    ]
    ds = Dataset(name="t", cases=cases)

    class _BoomChat:
        bot_public_id = "ch_boom"

        def ask(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    report = run(RunnerConfig(dataset=ds, tag="unit", chat=_BoomChat(), judge=None))
    assert report.total == 1 and report.passed_count == 0
    assert report.cases[0].error and "connection refused" in report.cases[0].error


def test_runner_calls_judge_only_when_rubric_present() -> None:
    cases = [
        GoldenCase(id="with", category="happy_path", lang="en", input="A", judge_rubric="be helpful"),
        GoldenCase(id="without", category="happy_path", lang="en", input="B"),
    ]
    ds = Dataset(name="t", cases=cases)
    chat = _FakeChat({"A": "ok answer A", "B": "ok answer B"})

    class _StubJudge:
        model = "stub"

        def __init__(self):
            self.calls = []

        def grade(self, case, output):
            self.calls.append(case.id)
            return JudgeResult(score=0.9, rationale="fine", model="stub")

    judge = _StubJudge()
    report = run(RunnerConfig(dataset=ds, tag="unit", chat=chat, judge=judge))
    assert judge.calls == ["with"]
    with_case = next(c for c in report.cases if c.case_id == "with")
    without_case = next(c for c in report.cases if c.case_id == "without")
    assert with_case.judge is not None and with_case.judge.score == 0.9
    assert without_case.judge is None


def test_runner_fails_case_when_judge_throws_for_case_with_rubric() -> None:
    """A judge outage (timeout / rate limit / SDK panic) on a case that
    has a rubric must NOT silently pass on the back of deterministic
    metrics — otherwise we lose regression detection during Anthropic
    incidents. Codex P1 review on #546."""

    cases = [
        GoldenCase(
            id="rubric_case",
            category="rag",
            lang="en",
            input="anything",
            must_contain=["ok"],
            judge_rubric="be helpful",
        ),
    ]
    ds = Dataset(name="t", cases=cases)
    chat = _FakeChat({"anything": "ok answer that satisfies must_contain"})

    class _ExplodingJudge:
        model = "stub"

        def grade(self, case, output):  # noqa: ARG002
            raise RuntimeError("anthropic 503 timeout")

    report = run(RunnerConfig(dataset=ds, tag="unit", chat=chat, judge=_ExplodingJudge()))
    case = report.cases[0]
    # Deterministic check passes …
    assert case.deterministic_passed
    # … but the case is still failed because the judge couldn't grade it.
    assert not case.overall_passed
    assert case.error is not None and "judge call failed" in case.error
    assert report.passed_count == 0


# ─── report rendering ───────────────────────────────────────────────────────


def test_markdown_report_contains_summary_and_table() -> None:
    report = RunReport(
        dataset="t",
        tag="unit",
        judge_model="claude-haiku-4-5-20251001",
        bot_public_id="ch_fake",
        started_at="2026-04-30T00:00:00+00:00",
        finished_at="2026-04-30T00:00:01+00:00",
        cases=[
            CaseResult(
                case_id="x",
                category="happy_path",
                lang="en",
                input="Q",
                output="A",
                latency_ms=100,
                metrics=[],
                judge=JudgeResult(score=0.8, rationale="ok", model="m"),
            )
        ],
    )
    md = render_markdown(report)
    assert "Eval run" in md and "ch_fake" in md and "0.80" in md and "| `x` |" in md
