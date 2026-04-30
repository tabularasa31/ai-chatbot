"""Deterministic eval metrics.

These run before the LLM-as-judge so we can fail-fast on hard
contracts (banned phrases, expected substrings, language mismatch).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.chat.language import detect_language
from backend.evals.dataset import GoldenCase


@dataclass(frozen=True)
class MetricResult:
    name: str
    passed: bool
    detail: str = ""


def check_must_contain(case: GoldenCase, output: str) -> MetricResult:
    if not case.must_contain:
        return MetricResult("must_contain", True, "skipped (none configured)")
    haystack = output.lower()
    missing = [p for p in case.must_contain if p.lower() not in haystack]
    if missing:
        return MetricResult("must_contain", False, f"missing: {missing}")
    return MetricResult("must_contain", True)


def check_must_not_contain(case: GoldenCase, output: str) -> MetricResult:
    if not case.must_not_contain:
        return MetricResult("must_not_contain", True, "skipped (none configured)")
    haystack = output.lower()
    found = [p for p in case.must_not_contain if p.lower() in haystack]
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
