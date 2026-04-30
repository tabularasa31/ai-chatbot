"""Diff two eval runs.

Used to produce before/after summaries for PR comments. Two
``report.json`` files in, one Markdown blob out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CaseDelta:
    case_id: str
    category: str
    lang: str
    before_passed: bool | None
    after_passed: bool | None
    before_judge: float | None
    after_judge: float | None

    @property
    def regressed(self) -> bool:
        return self.before_passed is True and self.after_passed is False

    @property
    def fixed(self) -> bool:
        return self.before_passed is False and self.after_passed is True

    @property
    def judge_delta(self) -> float | None:
        if self.before_judge is None or self.after_judge is None:
            return None
        return self.after_judge - self.before_judge


def load_report(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    return json.loads(p.read_text(encoding="utf-8"))


def diff(before: dict, after: dict) -> list[CaseDelta]:
    """Pair cases by ``case_id`` and emit one CaseDelta per case present
    in either side. Cases missing from one report show ``None`` for the
    missing side."""

    before_idx = {c["case_id"]: c for c in before.get("cases", [])}
    after_idx = {c["case_id"]: c for c in after.get("cases", [])}
    all_ids = sorted(set(before_idx) | set(after_idx))

    deltas: list[CaseDelta] = []
    for cid in all_ids:
        b = before_idx.get(cid)
        a = after_idx.get(cid)
        deltas.append(
            CaseDelta(
                case_id=cid,
                category=(a or b).get("category", ""),
                lang=(a or b).get("lang", ""),
                before_passed=b.get("overall_passed") if b else None,
                after_passed=a.get("overall_passed") if a else None,
                before_judge=_get_judge_score(b) if b else None,
                after_judge=_get_judge_score(a) if a else None,
            )
        )
    return deltas


def render_markdown(before: dict, after: dict, *, deltas: list[CaseDelta] | None = None) -> str:
    deltas = deltas if deltas is not None else diff(before, after)
    regressed = [d for d in deltas if d.regressed]
    fixed = [d for d in deltas if d.fixed]

    b_summary = before.get("summary", {})
    a_summary = after.get("summary", {})

    lines: list[str] = []
    lines.append(f"# Eval diff — `{after.get('dataset', '?')}`")
    lines.append("")
    lines.append(
        f"- **before:** `{before.get('tag', '?')}` ({b_summary.get('passed', 0)}/{b_summary.get('total', 0)} passed)"
    )
    lines.append(
        f"- **after:**  `{after.get('tag', '?')}` ({a_summary.get('passed', 0)}/{a_summary.get('total', 0)} passed)"
    )
    avg_before = b_summary.get("avg_judge_score")
    avg_after = a_summary.get("avg_judge_score")
    if avg_before is not None and avg_after is not None:
        delta = avg_after - avg_before
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"- **avg judge score:** {avg_before:.2f} → {avg_after:.2f} ({sign}{delta:.2f})"
        )
    lines.append(f"- **regressions:** {len(regressed)}")
    lines.append(f"- **fixes:** {len(fixed)}")
    lines.append("")

    if regressed:
        lines.append("## 🔴 Regressions")
        lines.append("")
        lines.append("| id | category | lang | before judge | after judge |")
        lines.append("|----|----------|------|-------------:|------------:|")
        for d in regressed:
            lines.append(
                f"| `{d.case_id}` | {d.category} | {d.lang} | "
                f"{_fmt(d.before_judge)} | {_fmt(d.after_judge)} |"
            )
        lines.append("")

    if fixed:
        lines.append("## 🟢 Fixes")
        lines.append("")
        lines.append("| id | category | lang | before judge | after judge |")
        lines.append("|----|----------|------|-------------:|------------:|")
        for d in fixed:
            lines.append(
                f"| `{d.case_id}` | {d.category} | {d.lang} | "
                f"{_fmt(d.before_judge)} | {_fmt(d.after_judge)} |"
            )
        lines.append("")

    if not regressed and not fixed:
        lines.append("_No pass/fail changes._")
        lines.append("")

    return "\n".join(lines) + "\n"


def _get_judge_score(case: dict) -> float | None:
    judge = case.get("judge")
    if not isinstance(judge, dict):
        return None
    score = judge.get("score")
    return float(score) if isinstance(score, (int, float)) else None


def _fmt(score: float | None) -> str:
    return f"{score:.2f}" if score is not None else "—"
