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
    MetricResult,
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


def test_must_contain_normalises_thousands_separators() -> None:
    """Real prod failure on #547: bot said `1,000 characters` but the
    case demanded substring `1000`. Both forms refer to the same
    number — strip thousands separators before comparing."""

    c = _case(must_contain=["1000"])
    # Comma (US/EN locale)
    assert check_must_contain(c, "Maximum 1,000 characters per message.").passed
    # Plain space
    assert check_must_contain(c, "Maximum 1 000 characters per message.").passed
    # NBSP (Russian-style typography for thousands)
    assert check_must_contain(c, "Максимум 1 000 символов на сообщение.").passed
    # And the unseparated form keeps working
    assert check_must_contain(c, "Maximum 1000 characters per message.").passed


def test_must_contain_does_not_collapse_separators_outside_numbers() -> None:
    """The normaliser must not change anything that isn't between two
    digits — otherwise we'd silently mutate prose (e.g. ``cap, then``
    becoming ``capthen``) and start matching things we shouldn't."""

    c = _case(must_contain=["limit, then"])
    # Caller asked for the exact prose snippet — we must NOT eat the comma+space.
    assert check_must_contain(c, "There is a limit, then a fallback.").passed
    # And we must NOT match if the snippet really is missing.
    assert not check_must_contain(c, "There is no such phrase here.").passed


def test_must_not_contain_also_normalises_numbers() -> None:
    """The same normalisation has to apply to the negative check, or
    a banned `1000` would slip through as `1,000` in the output."""

    c = _case(must_not_contain=["1000"])
    r = check_must_not_contain(c, "Cap is 1,000 messages.")
    assert not r.passed and "1000" in r.detail


def test_must_contain_does_not_collapse_decimal_comma() -> None:
    """In Russian / EU locales comma is the decimal separator, so
    `0,5` means 0.5 — NOT 05. Only collapse comma when it's followed
    by a canonical three-digit group (`1,000`), not before single or
    double-digit fractions (`0,5`, `0,55`)."""

    # The Russian-locale answer `0,5 секунды` must not start matching
    # the unrelated needle `05` after normalisation.
    c = _case(must_contain=["05"])
    assert not check_must_contain(c, "Задержка примерно 0,5 секунды.").passed

    # And the natural needle `0,5` keeps matching itself even though
    # its haystack is unchanged after normalisation.
    c = _case(must_contain=["0,5"])
    assert check_must_contain(c, "Задержка примерно 0,5 секунды.").passed


def test_must_contain_handles_chained_thousands_separators() -> None:
    """`1,000,000` and `1 000 000` should both collapse to `1000000`
    so a needle of `1000000` matches either form."""

    c = _case(must_contain=["1000000"])
    assert check_must_contain(c, "We saw 1,000,000 events.").passed
    assert check_must_contain(c, "We saw 1 000 000 events.").passed


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


# ─── ChatClient session_id forwarding ───────────────────────────────────────


class _StubStreamResp:
    """Minimal stand-in for an httpx streaming response."""

    def __init__(self) -> None:
        self.status_code = 200

    def __enter__(self):  # noqa: D401
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def iter_lines(self):
        yield 'data: {"type":"done","text":"ok","chat_ended":false}'


class _StubHttp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def stream(self, method: str, url: str, *, json: dict | None = None, params: dict | None = None):
        self.calls.append({"method": method, "url": url, "json": json, "params": params})
        return _StubStreamResp()


def test_chat_client_omits_session_id_when_caller_did_not_pass_one() -> None:
    """Regression: passing a random UUID to /widget/chat returns 409
    session_not_found because the backend treats session_id as 'resume
    this existing chat'. The runner must let the backend create the
    session (i.e. NOT include session_id at all)."""

    from backend.evals.client import ChatClient

    http = _StubHttp()
    client = ChatClient(bot_public_id="ch_test", http=http)
    client.ask("hello")
    assert http.calls[0]["params"] == {"bot_id": "ch_test"}
    assert "session_id" not in http.calls[0]["params"]


def test_chat_client_forwards_session_id_when_caller_passes_one() -> None:
    """Multi-turn callers must still be able to continue an existing
    conversation by passing session_id explicitly."""

    from backend.evals.client import ChatClient

    http = _StubHttp()
    client = ChatClient(bot_public_id="ch_test", http=http)
    client.ask("hello", session_id="00000000-0000-0000-0000-000000000123")
    assert http.calls[0]["params"] == {
        "bot_id": "ch_test",
        "session_id": "00000000-0000-0000-0000-000000000123",
    }


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
    assert "overall" in md  # explicit pass/fail column, not just deterministic


def test_markdown_report_overall_column_distinguishes_judge_vs_deterministic_failure() -> None:
    """Regression: previously the table only showed `det.` ✅/❌, so a
    case where deterministic checks passed but the judge scored below
    0.6 still appeared green in the table even though it was counted
    as failed in the run summary. Lock in that the new `overall`
    column reflects the real verdict."""

    metric = MetricResult(name="must_contain", passed=True)
    cases = [
        # Deterministic ✅, judge below 0.6 — overall must be ❌.
        CaseResult(
            case_id="judge_fail",
            category="rag",
            lang="en",
            input="Q",
            output="A",
            latency_ms=10,
            metrics=[metric],
            judge=JudgeResult(score=0.45, rationale="weak", model="m"),
        ),
        # Deterministic ✅, no judge run — overall must be ✅.
        CaseResult(
            case_id="all_pass",
            category="happy_path",
            lang="en",
            input="Q",
            output="A",
            latency_ms=10,
            metrics=[metric],
        ),
    ]
    report = RunReport(
        dataset="t",
        tag="unit",
        judge_model="m",
        bot_public_id="ch_fake",
        started_at="2026-04-30T00:00:00+00:00",
        finished_at="2026-04-30T00:00:01+00:00",
        cases=cases,
    )
    md = render_markdown(report)
    judge_fail_line = next(line for line in md.splitlines() if "judge_fail" in line)
    all_pass_line = next(line for line in md.splitlines() if "all_pass" in line)
    cells_judge_fail = [c.strip() for c in judge_fail_line.split("|")]
    cells_all_pass = [c.strip() for c in all_pass_line.split("|")]
    # Header: ['', 'id', 'category', 'lang', 'overall', 'det.', 'judge', 'latency', 'notes', '']
    assert cells_judge_fail[4] == "❌", f"overall column wrong: {judge_fail_line!r}"
    assert cells_judge_fail[5] == "✅", f"det. column wrong: {judge_fail_line!r}"
    assert cells_all_pass[4] == "✅"
    assert cells_all_pass[5] == "✅"
