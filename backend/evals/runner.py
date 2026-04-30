"""Top-level orchestration: dataset → chat → metrics → judge → report."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from backend.evals.client import ChatClient
from backend.evals.dataset import Dataset, GoldenCase
from backend.evals.judge import AnthropicJudge
from backend.evals.metrics import run_deterministic_metrics
from backend.evals.report import CaseResult, RunReport

logger = logging.getLogger(__name__)


@dataclass
class RunnerConfig:
    dataset: Dataset
    tag: str
    chat: ChatClient
    judge: AnthropicJudge | None
    """When ``None`` only deterministic metrics are scored. Useful for
    smoke runs / unit tests that don't want to hit Anthropic."""


def run(config: RunnerConfig) -> RunReport:
    """Execute every case in the dataset sequentially. Errors on a
    single case are captured per-case and never abort the run."""

    started = _now_iso()
    cases: list[CaseResult] = []
    for case in config.dataset.cases:
        cases.append(_run_case(case, config))
    finished = _now_iso()

    return RunReport(
        dataset=config.dataset.name,
        tag=config.tag,
        judge_model=config.judge.model if config.judge else None,
        bot_public_id=config.chat.bot_public_id,
        started_at=started,
        finished_at=finished,
        cases=cases,
    )


def _run_case(case: GoldenCase, config: RunnerConfig) -> CaseResult:
    try:
        chat_response = config.chat.ask(case.input)
    except Exception as exc:
        logger.warning("eval_case_chat_failed id=%s err=%s", case.id, exc)
        return CaseResult(
            case_id=case.id,
            category=case.category,
            lang=case.lang,
            input=case.input,
            output="",
            latency_ms=0,
            metrics=[],
            error=f"chat call failed: {exc}",
        )

    output = chat_response.text
    metrics = run_deterministic_metrics(case, output)

    judge_result = None
    if config.judge is not None and case.judge_rubric:
        try:
            judge_result = config.judge.grade(case, output)
        except Exception as exc:
            logger.warning("eval_case_judge_failed id=%s err=%s", case.id, exc)

    error_text: str | None = None
    if chat_response.error:
        error_text = (
            f"chat error: code={chat_response.error.get('code')} "
            f"msg={chat_response.error.get('message')}"
        )

    return CaseResult(
        case_id=case.id,
        category=case.category,
        lang=case.lang,
        input=case.input,
        output=output,
        latency_ms=chat_response.latency_ms,
        metrics=metrics,
        judge=judge_result,
        error=error_text,
    )


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
