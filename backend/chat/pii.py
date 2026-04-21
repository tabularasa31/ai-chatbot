"""
PII redaction helpers for outbound-safe text handling.

Stage 1 keeps a deterministic regex-only implementation and returns a
structured result so callers can persist redacted text and audit metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MANDATORY_ENTITY_TYPES = frozenset(
    {
        "EMAIL",
        "PHONE",
        "API_KEY",
        "PASSWORD",
        "CARD",
    }
)
OPTIONAL_ENTITY_TYPES = frozenset(
    {
        "ID_DOC",
        "IP",
        "URL_TOKEN",
    }
)
ALL_ENTITY_TYPES = MANDATORY_ENTITY_TYPES | OPTIONAL_ENTITY_TYPES
DEFAULT_OPTIONAL_ENTITY_TYPES = frozenset(OPTIONAL_ENTITY_TYPES)


@dataclass(frozen=True)
class DetectedEntitySummary:
    type: str
    count: int


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    entities_found: list[DetectedEntitySummary]
    was_redacted: bool


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

_PHONE_RE = re.compile(
    r"""
    (?:
        \+\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{1,4}[\s\-.]?\d{1,9}
        |
        \b8[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{2}[\s\-.]?\d{2}\b
        |
        \+\d{10,14}\b
    )
    """,
    re.VERBOSE,
)

_API_KEY_PATTERNS = [
    r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",
    r"\bsk-[A-Za-z0-9]{20,}\b",
    r"(?:token|api[_-]?key)\s*[:=]\s*[A-Za-z0-9\-_]{16,}",
    r"\b[A-Fa-f0-9]{32,}\b",
    r"\b[A-Za-z0-9+/]{40,}={0,2}\b",
]
_API_KEY_RE = re.compile("|".join(_API_KEY_PATTERNS), re.IGNORECASE)

_PASSWORD_RE = re.compile(
    r"(?:(?:password|passwd|pass|пароль)\s*(?:is|=|:)?\s+)([^\s,;]{4,})",
    re.IGNORECASE,
)

_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,18}\d\b")

_ID_DOC_PATTERNS = [
    # Russia
    r"(?:passport)\s*[№:]?\s*\d{2,4}[\s-]?\d{4,6}",
    r"(?:паспорт)\s*[№:]?\s*\d{2,4}[\s-]?\d{4,6}",
    r"(?:инн|снилс)\s*[№:]?\s*[\d\s-]{8,}",
    # USA — Social Security Number: requires keyword prefix to avoid 9-digit false positives
    r"\b(?:ssn|social security)\b\s*[:#№-]?\s*(?:\w{1,10}\s*[:#№-]?\s*)?(?:\d{3}-\d{2}-\d{4}|\d{9})\b",
    # ICAO machine-readable passport number: keyword + optional punctuation/connector word
    r"\b(?:passport|id)\b\s*[:#№-]?\s*(?:\w{1,10}\s*[:#№-]?\s*)?[A-Z]{1,2}\d{6,9}\b",
    # UK National Insurance: keyword prefix + space-separated format support (QQ 12 34 56 A)
    r"\b(?:ni|national insurance)\b\s*[:#№-]?\s*(?:\w{1,10}\s*[:#№-]?\s*)?[A-CEGHJ-PR-TW-Z]{2}(?:\s*\d){6}\s*[A-D]\b",
]
_ID_DOC_RE = re.compile("|".join(_ID_DOC_PATTERNS), re.IGNORECASE)

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

_URL_TOKEN_RE = re.compile(
    r"\bhttps?://[^\s]+?(?:token|api[_-]?key|access[_-]?token|auth|signature|sig)=([^\s&#]+)",
    re.IGNORECASE,
)


def _is_luhn_valid(raw: str) -> bool:
    digits = [int(ch) for ch in raw if ch.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _mask_cards(text: str) -> tuple[str, int]:
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        candidate = match.group(0)
        if not _is_luhn_valid(candidate):
            return candidate
        count += 1
        return "[CARD]"

    return _CARD_RE.sub(repl, text), count


def _mask_urls_with_tokens(text: str) -> tuple[str, int]:
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[URL_TOKEN]"

    return _URL_TOKEN_RE.sub(repl, text), count


def _mask_with_pattern(text: str, pattern: re.Pattern[str], placeholder: str) -> tuple[str, int]:
    matches = list(pattern.finditer(text))
    if not matches:
        return text, 0
    return pattern.sub(placeholder, text), len(matches)


def _mask_ips(text: str) -> tuple[str, int]:
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        candidate = match.group(0)
        parts = candidate.split(".")
        if all(len(part) == 1 for part in parts):
            return candidate
        try:
            octets = [int(part) for part in parts]
        except ValueError:
            return candidate
        if len(octets) != 4 or any(octet < 0 or octet > 255 for octet in octets):
            return candidate
        count += 1
        return "[IP]"

    return _IP_RE.sub(repl, text), count


def _enabled_entity_types(optional_entity_types: set[str] | None) -> set[str]:
    enabled = set(MANDATORY_ENTITY_TYPES)
    if optional_entity_types is None:
        enabled.update(DEFAULT_OPTIONAL_ENTITY_TYPES)
    else:
        enabled.update(entity for entity in optional_entity_types if entity in OPTIONAL_ENTITY_TYPES)
    return enabled


def redact(
    text: str,
    *,
    optional_entity_types: set[str] | None = None,
) -> RedactionResult:
    """
    Redact PII from text and return structured metadata.

    Mandatory entity types are always redacted. Optional entity types can be
    narrowed by passing `optional_entity_types`.
    """
    redacted_text = text
    enabled = _enabled_entity_types(optional_entity_types)
    counts: dict[str, int] = {}

    ordered_patterns: list[tuple[str, re.Pattern[str], str]] = [
        ("URL_TOKEN", _URL_TOKEN_RE, "[URL_TOKEN]"),
        ("API_KEY", _API_KEY_RE, "[API_KEY]"),
        ("PASSWORD", _PASSWORD_RE, "[PASSWORD]"),
        ("ID_DOC", _ID_DOC_RE, "[ID_DOC]"),
        ("IP", _IP_RE, "[IP]"),
        ("PHONE", _PHONE_RE, "[PHONE]"),
        ("EMAIL", _EMAIL_RE, "[EMAIL]"),
    ]

    for entity_type, pattern, placeholder in ordered_patterns:
        if entity_type not in enabled:
            continue
        if entity_type == "URL_TOKEN":
            redacted_text, count = _mask_urls_with_tokens(redacted_text)
        elif entity_type == "IP":
            redacted_text, count = _mask_ips(redacted_text)
        else:
            redacted_text, count = _mask_with_pattern(redacted_text, pattern, placeholder)
        if count:
            counts[entity_type] = counts.get(entity_type, 0) + count

    if "CARD" in enabled:
        redacted_text, count = _mask_cards(redacted_text)
        if count:
            counts["CARD"] = counts.get("CARD", 0) + count

    ordered_entities = [
        DetectedEntitySummary(type=entity_type, count=count)
        for entity_type, count in counts.items()
    ]
    return RedactionResult(
        redacted_text=redacted_text,
        entities_found=ordered_entities,
        was_redacted=bool(counts),
    )


def redact_text(
    text: str,
    *,
    optional_entity_types: set[str] | None = None,
) -> str:
    """Convenience wrapper returning only the redacted text."""
    return redact(text, optional_entity_types=optional_entity_types).redacted_text
