"""Deterministic eval metrics.

These run before the LLM-as-judge so we can fail-fast on hard
contracts (banned phrases, expected substrings, language mismatch).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.chat.language import detect_language
from backend.evals.dataset import GoldenCase


@dataclass(frozen=True)
class MetricResult:
    name: str
    passed: bool
    detail: str = ""


# Strip common thousands separators that sit BETWEEN two digits — `1,000`,
# `1 000`, `1<NBSP>000` all collapse to `1000`. Anything else (commas in
# prose, spaces between words, etc.) is left untouched.
_DIGIT_SEPARATOR_RE = re.compile(r"(?<=\d)[,\s](?=\d)")


def _normalize_for_match(text: str) -> str:
    """Lowercase + collapse thousands separators inside numbers so that
    a needle of ``1000`` matches a haystack of ``1,000`` or ``1 000``.

    Without this, golden cases like `must_contain: ["1000"]` would
    falsely fail when the bot writes the value in localised form, even
    though the LLM judge correctly considered the answer right."""

    return _DIGIT_SEPARATOR_RE.sub("", text.lower())


def check_must_contain(case: GoldenCase, output: str) -> MetricResult:
    if not case.must_contain:
        return MetricResult("must_contain", True, "skipped (none configured)")
    haystack = _normalize_for_match(output)
    missing = [p for p in case.must_contain if _normalize_for_match(p) not in haystack]
    if missing:
        return MetricResult("must_contain", False, f"missing: {missing}")
    return MetricResult("must_contain", True)


def check_must_not_contain(case: GoldenCase, output: str) -> MetricResult:
    if not case.must_not_contain:
        return MetricResult("must_not_contain", True, "skipped (none configured)")
    haystack = _normalize_for_match(output)
    found = [p for p in case.must_not_contain if _normalize_for_match(p) in haystack]
    if found:
        return MetricResult("must_not_contain", False, f"found banned: {found}")
    return MetricResult("must_not_contain", True)


def check_language(case: GoldenCase, output: str) -> MetricResult:
    expected = case.expected_lang or case.lang
    if expected == "any":
        return MetricResult("language", True, "skipped (any)")
    if not output.strip():
        return MetricResult("language", False, "empty output")
    result = detect_language(output)
    detected = result.detected_language
    if detected == "unknown" or not result.is_reliable:
        return MetricResult("language", False, f"unreliable detection: {detected}")
    if detected != expected:
        return MetricResult("language", False, f"expected={expected} got={detected}")
    return MetricResult("language", True, f"detected={detected}")


def run_deterministic_metrics(case: GoldenCase, output: str) -> list[MetricResult]:
    return [
        check_must_contain(case, output),
        check_must_not_contain(case, output),
        check_language(case, output),
    ]
