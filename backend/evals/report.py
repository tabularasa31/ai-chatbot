"""Run-result aggregation and report writers.

A run produces one ``RunReport`` aggregating per-case metrics and
judge scores. We emit two artefacts:

- ``report.json`` — full machine-readable record (used by PR #3 to
  upload runs to Langfuse and to compute before/after diffs)
- ``report.md`` — terse human summary suitable for PR comments and
  console output
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import fmean

from backend.evals.dataset import GoldenCase
from backend.evals.judge import JudgeResult
from backend.evals.metrics import MetricResult


@dataclass
class TurnTrace:
    """Per-turn record for chain (multi-turn) cases. Single-turn cases
    have an empty trace list."""

    turn: int
    user: str
    bot: str
    latency_ms: int
    escalation_offered: bool


@dataclass
class CaseResult:
    case_id: str
    category: str
    lang: str
    input: str
    output: str
    latency_ms: int
    metrics: list[MetricResult] = field(default_factory=list)
    judge: JudgeResult | None = None
    error: str | None = None
    turns_trace: list[TurnTrace] = field(default_factory=list)
    escalation_offered_at_turn: int | None = None

    @property
    def deterministic_passed(self) -> bool:
        return all(m.passed for m in self.metrics)

    @property
    def overall_passed(self) -> bool:
        if self.error:
            return False
        if not self.deterministic_passed:
            return False
        if self.judge is not None and self.judge.score < 0.6:
            return False
        return True


@dataclass
class RunReport:
    dataset: str
    tag: str
    judge_model: str | None
    bot_public_id: str
    started_at: str
    finished_at: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.overall_passed)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def avg_judge_score(self) -> float | None:
        scores = [c.judge.score for c in self.cases if c.judge is not None]
        return fmean(scores) if scores else None

    @property
    def avg_latency_ms(self) -> float | None:
        if not self.cases:
            return None
        return fmean(c.latency_ms for c in self.cases)


def write_json(report: RunReport, out: str | Path) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_serializable(report), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def write_markdown(report: RunReport, out: str | Path) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_markdown(report), encoding="utf-8")
    return p


def render_markdown(report: RunReport) -> str:
    lines: list[str] = []
    lines.append(f"# Eval run — `{report.dataset}` ({report.tag})")
    lines.append("")
    lines.append(f"- bot_public_id: `{report.bot_public_id}`")
    lines.append(f"- started: {report.started_at}")
    lines.append(f"- finished: {report.finished_at}")
    lines.append(f"- judge_model: `{report.judge_model or '—'}`")
    lines.append(f"- **passed:** {report.passed_count}/{report.total}")
    if report.avg_judge_score is not None:
        lines.append(f"- avg judge score: **{report.avg_judge_score:.2f}**")
    if report.avg_latency_ms is not None:
        lines.append(f"- avg latency: {report.avg_latency_ms:.0f} ms")
    lines.append("")
    lines.append("| id | category | lang | overall | det. | judge | latency | notes |")
    lines.append("|----|----------|------|:-------:|:----:|:-----:|--------:|-------|")
    for c in report.cases:
        overall = "✅" if c.overall_passed else "❌"
        det = "✅" if c.deterministic_passed else "❌"
        judge = f"{c.judge.score:.2f}" if c.judge else "—"
        notes_parts: list[str] = []
        if c.error:
            notes_parts.append(f"error: {c.error}")
        for m in c.metrics:
            if not m.passed and m.detail:
                notes_parts.append(f"{m.name}: {m.detail}")
        if c.judge and c.judge.rationale:
            notes_parts.append(c.judge.rationale)
        notes = "; ".join(notes_parts).replace("|", "\\|")
        lines.append(
            f"| `{c.case_id}` | {c.category} | {c.lang} | {overall} | {det} | "
            f"{judge} | {c.latency_ms} | {notes} |"
        )
    # Chain transcripts — collapsed by default, expanded only when present.
    chain_cases = [c for c in report.cases if c.turns_trace]
    if chain_cases:
        lines.append("")
        lines.append("## Chain transcripts")
        for c in chain_cases:
            offered = (
                f"turn {c.escalation_offered_at_turn}"
                if c.escalation_offered_at_turn is not None
                else "never"
            )
            lines.append("")
            lines.append(f"### `{c.case_id}` — escalation offered: {offered}")
            for t in c.turns_trace:
                marker = " 🚨" if t.escalation_offered else ""
                lines.append(f"- **turn {t.turn}** ({t.latency_ms} ms){marker}")
                lines.append(f"  - 👤 {t.user}")
                lines.append(f"  - 🤖 {t.bot}")
    return "\n".join(lines) + "\n"


def _serializable(report: RunReport) -> dict:
    """Convert the report graph to plain dicts for JSON dumping."""

    def case_dict(c: CaseResult) -> dict:
        return {
            "case_id": c.case_id,
            "category": c.category,
            "lang": c.lang,
            "input": c.input,
            "output": c.output,
            "latency_ms": c.latency_ms,
            "metrics": [asdict(m) for m in c.metrics],
            "judge": asdict(c.judge) if c.judge is not None else None,
            "error": c.error,
            "deterministic_passed": c.deterministic_passed,
            "overall_passed": c.overall_passed,
            "turns_trace": [asdict(t) for t in c.turns_trace],
            "escalation_offered_at_turn": c.escalation_offered_at_turn,
        }

    return {
        "dataset": report.dataset,
        "tag": report.tag,
        "judge_model": report.judge_model,
        "bot_public_id": report.bot_public_id,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "summary": {
            "passed": report.passed_count,
            "total": report.total,
            "avg_judge_score": report.avg_judge_score,
            "avg_latency_ms": report.avg_latency_ms,
        },
        "cases": [case_dict(c) for c in report.cases],
    }


# Reference unused import for type-checkers when GoldenCase is consumed elsewhere.
_ = GoldenCase
