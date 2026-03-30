from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionDetectionResult:
    detected: bool
    pattern: str | None = None


_PATTERNS: tuple[str, ...] = (
    r"ignore\s+(previous|all|your)\s+instructions?",
    r"\bforget\s+(everything|what|your|all)\b",
    r"\byou are now\s+(a|an|the)\b",
    r"act as\s+(a|an|the)?\s+\w+",
    r"(disregard|override|bypass)\s+(your|all|previous|the)\b",
    r"pretend\s+(you are|to be)\b",
    r"\byour\s+(new|real|true|actual)\s+(role|purpose|instructions|prompt)\b",
    r"(reveal|show|print|output|repeat)\s+(your|the)\s+(system|original)?\s*(prompt|instructions|context)\b",
    r"(jailbreak|dan mode|developer mode|unrestricted mode)\b",
    r"\[system\]|\<system\>\s*|###\s*system",
)


_COMPILED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, flags=re.IGNORECASE), p) for p in _PATTERNS
)


def detect_prompt_injection(text: str) -> InjectionDetectionResult:
    """Regex-only injection detection (no LLM)."""
    if not text:
        return InjectionDetectionResult(detected=False, pattern=None)

    for rx, pattern in _COMPILED:
        if rx.search(text):
            return InjectionDetectionResult(detected=True, pattern=pattern)
    return InjectionDetectionResult(detected=False, pattern=None)

