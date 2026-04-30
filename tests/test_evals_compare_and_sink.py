"""Unit tests for backend.evals.compare and backend.evals.langfuse_sink.

These exercise the diff/markdown logic and the Langfuse sink with a
mocked Langfuse client — no real network calls.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from backend.evals.compare import (
    CaseDelta,
    diff,
    load_report,
    render_markdown,
)
from backend.evals.dataset import Dataset, GoldenCase
from backend.evals.judge import JudgeResult
from backend.evals.langfuse_sink import upload_dataset, upload_run
from backend.evals.report import (
    CaseResult,
    RunReport,
    write_json,
)


# ─── compare ────────────────────────────────────────────────────────────────


def _make_report(tag: str, cases: list[dict]) -> dict:
    """Build a serialised report dict with the same shape as
    backend.evals.report.write_json output."""

    passed = sum(1 for c in cases if c.get("overall_passed"))
    return {
        "dataset": "t",
        "tag": tag,
        "judge_model": "claude-haiku-4-5-20251001",
        "bot_public_id": "ch_test",
        "started_at": "2026-04-30T00:00:00+00:00",
        "finished_at": "2026-04-30T00:00:01+00:00",
        "summary": {
            "passed": passed,
            "total": len(cases),
            "avg_judge_score": (
                sum(
                    (c.get("judge") or {}).get("score", 0.0)
                    for c in cases
                    if c.get("judge")
                )
                / max(1, sum(1 for c in cases if c.get("judge")))
            )
            if any(c.get("judge") for c in cases)
            else None,
            "avg_latency_ms": 100,
        },
        "cases": cases,
    }


def _case_dict(case_id: str, *, passed: bool, judge_score: float | None = None) -> dict:
    return {
        "case_id": case_id,
        "category": "happy_path",
        "lang": "en",
        "input": "Q",
        "output": "A",
        "latency_ms": 100,
        "metrics": [],
        "judge": (
            {"score": judge_score, "rationale": "ok", "model": "m"}
            if judge_score is not None
            else None
        ),
        "error": None,
        "deterministic_passed": passed,
        "overall_passed": passed,
    }


def test_diff_pairs_cases_by_id_and_flags_regressions_and_fixes() -> None:
    before = _make_report(
        "before",
        [
            _case_dict("a", passed=True, judge_score=0.9),
            _case_dict("b", passed=True, judge_score=0.8),
            _case_dict("c", passed=False, judge_score=0.3),
        ],
    )
    after = _make_report(
        "after",
        [
            _case_dict("a", passed=True, judge_score=0.95),  # stayed green
            _case_dict("b", passed=False, judge_score=0.4),  # regressed
            _case_dict("c", passed=True, judge_score=0.7),  # fixed
        ],
    )
    deltas = diff(before, after)
    by_id = {d.case_id: d for d in deltas}
    assert not by_id["a"].regressed and not by_id["a"].fixed
    assert by_id["b"].regressed and not by_id["b"].fixed
    assert by_id["c"].fixed and not by_id["c"].regressed
    # Judge delta arithmetic
    assert pytest.approx(by_id["a"].judge_delta, abs=1e-9) == 0.05
    assert pytest.approx(by_id["c"].judge_delta, abs=1e-9) == 0.4


def test_diff_handles_cases_only_on_one_side() -> None:
    before = _make_report("b", [_case_dict("only_before", passed=True)])
    after = _make_report("a", [_case_dict("only_after", passed=False)])
    deltas = diff(before, after)
    by_id = {d.case_id: d for d in deltas}
    # Both half-present: no regression/fix, just None on the missing side.
    assert by_id["only_before"].before_passed is True
    assert by_id["only_before"].after_passed is None
    assert by_id["only_after"].before_passed is None
    assert by_id["only_after"].after_passed is False


def test_render_markdown_includes_regressions_and_fixes_sections() -> None:
    before = _make_report(
        "before",
        [
            _case_dict("ok", passed=True, judge_score=0.9),
            _case_dict("regressed", passed=True, judge_score=0.8),
            _case_dict("fixed", passed=False, judge_score=0.3),
        ],
    )
    after = _make_report(
        "after",
        [
            _case_dict("ok", passed=True, judge_score=0.9),
            _case_dict("regressed", passed=False, judge_score=0.4),
            _case_dict("fixed", passed=True, judge_score=0.7),
        ],
    )
    md = render_markdown(before, after)
    assert "Regressions" in md
    assert "regressed" in md
    assert "Fixes" in md
    assert "fixed" in md


def test_render_markdown_says_no_changes_when_identical() -> None:
    cases = [_case_dict("a", passed=True, judge_score=0.9)]
    md = render_markdown(_make_report("b", cases), _make_report("a", cases))
    assert "No pass/fail changes" in md


def test_load_report_reads_json_written_by_runner(tmp_path) -> None:
    """End-to-end: a report written by report.write_json must round-trip
    through compare.load_report and diff()."""

    report = RunReport(
        dataset="t",
        tag="unit",
        judge_model="m",
        bot_public_id="ch_x",
        started_at="2026-04-30T00:00:00+00:00",
        finished_at="2026-04-30T00:00:01+00:00",
        cases=[
            CaseResult(
                case_id="a",
                category="happy_path",
                lang="en",
                input="Q",
                output="A",
                latency_ms=10,
                metrics=[],
                judge=JudgeResult(score=0.9, rationale="ok", model="m"),
            ),
        ],
    )
    path = write_json(report, tmp_path / "report.json")
    loaded = load_report(path)
    deltas = diff(loaded, loaded)
    assert deltas[0].case_id == "a"
    assert deltas[0].judge_delta == 0.0
    assert not deltas[0].regressed
    assert not deltas[0].fixed


# ─── langfuse sink ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests don't pick up real Langfuse creds from the dev shell."""
    for var in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_upload_dataset_is_no_op_without_creds() -> None:
    ds = Dataset(name="t", cases=[GoldenCase(id="x", category="happy_path", lang="en", input="Q")])
    assert upload_dataset(ds) is False


def test_upload_run_is_no_op_without_creds() -> None:
    report = RunReport(
        dataset="t",
        tag="unit",
        judge_model=None,
        bot_public_id="ch_x",
        started_at="2026-04-30T00:00:00+00:00",
        finished_at="2026-04-30T00:00:01+00:00",
        cases=[],
    )
    assert upload_run(report) is False


def test_upload_dataset_creates_dataset_then_items_when_client_provided() -> None:
    client = MagicMock()
    ds = Dataset(
        name="t",
        description="hello",
        cases=[
            GoldenCase(
                id="x",
                category="happy_path",
                lang="en",
                input="Q",
                must_contain=["q"],
                judge_rubric="be helpful",
            )
        ],
    )
    assert upload_dataset(ds, client=client) is True
    client.create_dataset.assert_called_once_with(name="t", description="hello")
    client.create_dataset_item.assert_called_once()
    args, kwargs = client.create_dataset_item.call_args
    assert kwargs["dataset_name"] == "t"
    assert kwargs["id"] == "x"
    assert kwargs["input"] == {"question": "Q", "lang": "en"}
    assert kwargs["expected_output"] == {"judge_rubric": "be helpful"}
    assert kwargs["metadata"]["category"] == "happy_path"


def test_upload_dataset_tolerates_create_dataset_error() -> None:
    """If the dataset already exists Langfuse may raise — the sink should
    still proceed to upsert items."""

    client = MagicMock()
    client.create_dataset.side_effect = RuntimeError("already exists")
    ds = Dataset(name="t", cases=[GoldenCase(id="x", category="happy_path", lang="en", input="Q")])
    assert upload_dataset(ds, client=client) is True
    client.create_dataset_item.assert_called_once()


def test_upload_run_emits_a_trace_and_scores_per_case() -> None:
    client = MagicMock()
    trace = MagicMock(id="trace-123")
    client.trace.return_value = trace

    report = RunReport(
        dataset="t",
        tag="run-1",
        judge_model="claude-haiku-4-5-20251001",
        bot_public_id="ch_x",
        started_at="2026-04-30T00:00:00+00:00",
        finished_at="2026-04-30T00:00:01+00:00",
        cases=[
            CaseResult(
                case_id="case_a",
                category="rag",
                lang="en",
                input="Q",
                output="A",
                latency_ms=50,
                metrics=[],
                judge=JudgeResult(score=0.85, rationale="good", model="m"),
            ),
            CaseResult(
                case_id="case_b",
                category="happy_path",
                lang="en",
                input="Q2",
                output="A2",
                latency_ms=60,
                metrics=[],
                judge=None,
            ),
        ],
    )
    assert upload_run(report, client=client) is True

    # 2 cases → 2 traces created
    assert client.trace.call_count == 2

    # Score breakdown:
    #   case_a:  overall_passed + deterministic_passed + judge_score = 3
    #   case_b:  overall_passed + deterministic_passed             = 2
    assert client.score.call_count == 5

    score_names = {kwargs["name"] for _, kwargs in client.score.call_args_list}
    assert score_names == {"overall_passed", "deterministic_passed", "judge_score"}


def test_upload_run_continues_when_individual_trace_fails() -> None:
    """A flaky Langfuse call on one case must not abort the whole upload."""

    client = MagicMock()
    client.trace.side_effect = [RuntimeError("network blip"), MagicMock(id="t2")]

    report = RunReport(
        dataset="t",
        tag="run-1",
        judge_model=None,
        bot_public_id="ch_x",
        started_at="2026-04-30T00:00:00+00:00",
        finished_at="2026-04-30T00:00:01+00:00",
        cases=[
            CaseResult(
                case_id="boom",
                category="rag",
                lang="en",
                input="Q",
                output="A",
                latency_ms=10,
            ),
            CaseResult(
                case_id="ok",
                category="rag",
                lang="en",
                input="Q",
                output="A",
                latency_ms=10,
            ),
        ],
    )
    assert upload_run(report, client=client) is True
    # Even though the first trace blew up, the second one ran and got scores.
    assert client.score.call_count >= 2


def test_upload_dataset_picks_up_env_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test of the credential-detection path: setting the three
    env vars should cause _client_or_none to construct a Langfuse
    client with those values."""

    monkeypatch.setenv("LANGFUSE_HOST", "https://example.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    captured: dict[str, object] = {}
    fake_client = MagicMock()

    def fake_factory(*args: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return fake_client

    # Patch the symbol that's imported inside _client_or_none.
    import langfuse as _lf

    monkeypatch.setattr(_lf, "Langfuse", fake_factory)

    ds = Dataset(name="t", cases=[GoldenCase(id="x", category="happy_path", lang="en", input="Q")])
    assert upload_dataset(ds) is True
    assert captured == {
        "host": "https://example.com",
        "public_key": "pk",
        "secret_key": "sk",
    }
    fake_client.create_dataset.assert_called_once()
    fake_client.create_dataset_item.assert_called_once()


# Placeholder usage so a strict linter doesn't complain about the import.
_ = (CaseDelta, os)
