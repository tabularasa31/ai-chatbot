"""Top-level orchestration: dataset → chat → metrics → judge → report."""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

from backend.evals.client import ChatClient
from backend.evals.dataset import Dataset, GoldenCase
from backend.evals.judge import AnthropicJudge
from backend.evals.metrics import MetricResult, run_deterministic_metrics
from backend.evals.report import CaseResult, RunReport, TurnTrace

logger = logging.getLogger(__name__)


# Escalation-offer detector. We don't have an explicit `escalation_offered`
# flag in the widget response, so we match the pre-confirm prompt heuristic:
# the bot ends a turn by asking whether to forward to support / open a ticket.
# Patterns are intentionally permissive (the LLM phrases vary) but require
# both an action verb (forward/open/translate to ticket) and a support-team
# noun so that an unrelated mention of "ticket" in an answer body doesn't
# trip the check.
_ESCALATION_OFFER_PATTERNS = [
    # Russian
    re.compile(r"(перешл[аёе]?|откр[ыо]ть?|создать?|отправ[ит]ь?).{0,40}(тикет|поддержк|обращени)", re.IGNORECASE),
    re.compile(r"(передать|переслать).{0,40}(поддержк|команд)", re.IGNORECASE),
    # English
    re.compile(r"(open|file|create|raise|forward).{0,40}(ticket|support|case)", re.IGNORECASE),
    re.compile(r"(would you like|want me to|shall i).{0,80}(support|ticket|team)", re.IGNORECASE),
]


def _looks_like_escalation_offer(text: str) -> bool:
    return any(p.search(text) for p in _ESCALATION_OFFER_PATTERNS)


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
    is_chain = bool(case.turns)
    messages = case.messages
    session_id: str | None = None
    turns_trace: list[TurnTrace] = []
    total_latency_ms = 0
    escalation_offered_at_turn: int | None = None

    if is_chain:
        try:
            session_id = config.chat.start_session()
        except Exception as exc:
            logger.warning("eval_case_session_init_failed id=%s err=%s", case.id, exc)
            return CaseResult(
                case_id=case.id,
                category=case.category,
                lang=case.lang,
                input=" | ".join(messages),
                output="",
                latency_ms=0,
                metrics=[],
                error=f"session init failed: {exc}",
                turns_trace=[],
            )

    last_response = None
    for turn_idx, message in enumerate(messages, start=1):
        try:
            chat_response = config.chat.ask(message, session_id=session_id)
        except Exception as exc:
            logger.warning("eval_case_chat_failed id=%s turn=%d err=%s", case.id, turn_idx, exc)
            return CaseResult(
                case_id=case.id,
                category=case.category,
                lang=case.lang,
                input=message if not is_chain else " | ".join(messages),
                output="",
                latency_ms=total_latency_ms,
                metrics=[],
                error=f"chat call failed at turn {turn_idx}: {exc}",
                turns_trace=turns_trace,
            )
        last_response = chat_response
        total_latency_ms += chat_response.latency_ms
        offered = _looks_like_escalation_offer(chat_response.text)
        turns_trace.append(
            TurnTrace(
                turn=turn_idx,
                user=message,
                bot=chat_response.text,
                latency_ms=chat_response.latency_ms,
                escalation_offered=offered,
            )
        )
        if offered and escalation_offered_at_turn is None:
            escalation_offered_at_turn = turn_idx

    assert last_response is not None  # messages is always >= 1 by validator
    output = last_response.text
    metrics = run_deterministic_metrics(case, output)
    if case.expected_escalation_offered_by_turn is not None:
        metrics.append(
            _check_escalation_offer(case, escalation_offered_at_turn),
        )

    judge_result = None
    judge_error: str | None = None
    if config.judge is not None and case.judge_rubric:
        try:
            judge_result = config.judge.grade(case, output)
        except Exception as exc:
            logger.warning("eval_case_judge_failed id=%s err=%s", case.id, exc)
            judge_error = f"judge call failed: {exc}"
        else:
            if judge_result is None:
                # Defensive: grade() returns None only when a rubric is
                # absent, which we already gated on above. If it ever
                # comes back here, treat it as a graded failure rather
                # than a silent pass.
                judge_error = "judge returned no result for case with rubric"

    error_text: str | None = None
    if last_response.error:
        error_text = (
            f"chat error: code={last_response.error.get('code')} "
            f"msg={last_response.error.get('message')}"
        )
    elif judge_error is not None:
        # Surface judge outages as a per-case error so `overall_passed`
        # short-circuits to False — otherwise an Anthropic timeout could
        # silently turn a regression into a green run.
        error_text = judge_error

    return CaseResult(
        case_id=case.id,
        category=case.category,
        lang=case.lang,
        input=" | ".join(messages) if is_chain else messages[0],
        output=output,
        latency_ms=total_latency_ms,
        metrics=metrics,
        judge=judge_result,
        error=error_text,
        turns_trace=turns_trace,
        escalation_offered_at_turn=escalation_offered_at_turn,
    )


def _check_escalation_offer(
    case: GoldenCase,
    offered_at: int | None,
) -> MetricResult:
    """Verify chain-case escalation behaviour against
    ``expected_escalation_offered_by_turn``:

    - Positive N → escalation must be offered at turn ≤ N
    - -1         → escalation must NOT be offered (control cases)
    """
    expected = case.expected_escalation_offered_by_turn
    if expected is None:
        return MetricResult("escalation_offer", True, "skipped (no expectation)")
    if expected == -1:
        if offered_at is None:
            return MetricResult("escalation_offer", True, "no offer (as expected)")
        return MetricResult(
            "escalation_offer",
            False,
            f"unexpected offer at turn {offered_at}",
        )
    # Positive expectation: must be offered by turn `expected`.
    if offered_at is None:
        return MetricResult(
            "escalation_offer",
            False,
            f"expected offer by turn {expected}, never offered",
        )
    if offered_at > expected:
        return MetricResult(
            "escalation_offer",
            False,
            f"offered too late: turn {offered_at}, expected ≤ {expected}",
        )
    return MetricResult(
        "escalation_offer",
        True,
        f"offered at turn {offered_at} (≤ {expected})",
    )


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
