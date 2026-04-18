"""Markdown draft builders for Mode A and Mode B gap items."""

from __future__ import annotations


def _build_mode_a_draft_markdown(*, label: str, example_questions: list[str]) -> str:
    lines = [
        f"# {label}",
        "",
        "## Why this matters",
        "This docs gap was detected from the current knowledge base and needs explicit coverage.",
    ]
    if example_questions:
        lines.extend(["", "## Example questions"])
        lines.extend([f"- {question}" for question in example_questions])
    lines.extend(["", "## Draft notes", "- Add a concise overview", "- Explain the main workflow", "- Link related limits, edge cases, and troubleshooting"])
    return "\n".join(lines)


def _build_mode_b_draft_markdown(
    *,
    label: str,
    sample_questions: list[str],
    linked_mode_a_questions: list[str],
    coverage_score: float | None,
    signal_weight: float | None,
) -> str:
    lines = [
        f"# {label}",
        "",
        "## User signal",
        f"- Aggregate signal weight: {signal_weight or 0.0:.1f}",
        f"- Coverage score: {coverage_score:.2f}" if coverage_score is not None else "- Coverage score: unknown",
    ]
    if sample_questions:
        lines.extend(["", "## Sample user questions"])
        lines.extend([f"- {question}" for question in sample_questions])
    if linked_mode_a_questions:
        lines.extend(["", "## Also missing in docs"])
        lines.extend([f"- {question}" for question in linked_mode_a_questions])
    lines.extend(["", "## Draft notes", "- Start from the user pain in the questions above", "- Document the exact workflow or limitation", "- Include prerequisites, examples, and common failure cases"])
    return "\n".join(lines)
