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


# Collapse thousands separators between digits so `1,000`, `1 000`
# and `1<NBSP>000` all match `1000`.
#
# Comma is restricted to the canonical thousands shape — separator
# followed by exactly three digits that aren't themselves followed by
# another digit — because in Russian / many EU locales comma is also
# the decimal separator (`0,5` means 0.5). The 3-digit lookahead lets
# `1,000` and `1,000,000` collapse but leaves `0,5` and `0,55` alone.
#
# Whitespace (regular space + NBSP via Unicode `\s`) only ever signals
# thousands grouping inside numbers in our domain, so we collapse it
# whenever it sits between two digits.
_THOUSANDS_COMMA_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_DIGIT_SPACE_RE = re.compile(r"(?<=\d)\s(?=\d)")


def _normalize_for_match(text: str) -> str:
    """Lowercase + collapse thousands separators inside numbers so that
    a needle of ``1000`` matches a haystack of ``1,000`` or ``1 000``.

    Without this, golden cases like `must_contain: ["1000"]` would
    falsely fail when the bot writes the value in localised form, even
    though the LLM judge correctly considered the answer right."""

    out = text.lower()
    out = _THOUSANDS_COMMA_RE.sub("", out)
    out = _DIGIT_SPACE_RE.sub("", out)
    return out


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
