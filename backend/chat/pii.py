"""PII redaction at the model egress boundary.

One rule governs this module: nothing sensitive reaches the model. Stored text
keeps the visitor's original wording, and every path that hands text to OpenAI
— the question, the chat history in the prompt, escalation transcripts,
background jobs — masks it here first. Correspondence masks nothing: the
dashboard, the operator inbox and the support e-mail body render the stored
original in full.

Detection is structural, and therefore language-independent by construction. An
e-mail address, a phone number, a card number, an IP, a tokenised URL and an
API key have the same shape in every language, so nothing here matches a word.

Out of scope by design: values that only a label marks as sensitive, such as a
credential or a document number. Matching those needs the label, the label is
in the visitor's language, and no rule here may depend on a language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Every entity type this module masks, highest priority first. Overlapping
# matches are merged and the merged span takes the label of its widest member;
# this order breaks ties between members of equal width.
_PRIORITY = (
    "URL_TOKEN",
    "API_KEY",
    "EMAIL",
    "PHONE",
    "IP",
    "CARD",
)


@dataclass(frozen=True)
class DetectedEntitySummary:
    type: str
    count: int


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    entities_found: list[DetectedEntitySummary]
    was_redacted: bool


@dataclass(frozen=True)
class EntitySpan:
    start: int
    end: int
    entity_type: str
    # Index into _PRIORITY, used to break ties when merging equal-width spans.
    rank: int = 0


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

# Digit groups joined by single spaces or hyphens — how a card is written,
# either as one block or as 4-4-4-4.
_SEPARATED_GROUPS_RE = re.compile(r"\d+(?:[ -]\d+)*")

_CARD_MIN_DIGITS = 13
_CARD_MAX_DIGITS = 19

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

_URL_TOKEN_RE = re.compile(
    r"\bhttps?://[^\s]+?(?:token|api[_-]?key|access[_-]?token|auth|signature|sig)=([^\s&#]+)",
    re.IGNORECASE,
)

_Span = tuple[int, int]


def _regex_spans(pattern: re.Pattern[str], text: str) -> list[_Span]:
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


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


def _card_spans(text: str, blocked: list[EntitySpan]) -> list[_Span]:
    """Cards, searched for in the digit groups no other detector has claimed.

    ``blocked`` carries the matches of every higher-priority detector. Their
    digits are excluded before the search, because a phone number or an IP
    written next to a card forms one digit run with it: searching the run whole
    would test the wrong number, and a long enough run eventually yields a
    checksum-valid slice that spans both.
    """
    groups = [
        (m.start(), m.end())
        for m in re.finditer(r"\d+", text)
        if not any(taken.start < m.end() and m.start() < taken.end for taken in blocked)
    ]

    spans: list[_Span] = []
    first = 0
    while first < len(groups):
        match_end: int | None = None
        for last in range(len(groups), first, -1):
            digits = sum(b - a for a, b in groups[first:last])
            if digits > _CARD_MAX_DIGITS:
                continue
            if digits < _CARD_MIN_DIGITS:
                break
            candidate = text[groups[first][0] : groups[last - 1][1]]
            # Groups must be adjacent in the text to be one written number.
            if _SEPARATED_GROUPS_RE.fullmatch(candidate) and _is_luhn_valid(candidate):
                spans.append((groups[first][0], groups[last - 1][1]))
                match_end = last
                break
        first = match_end if match_end is not None else first + 1
    return spans


def _ip_spans(text: str) -> list[_Span]:
    spans: list[_Span] = []
    for match in _IP_RE.finditer(text):
        parts = match.group(0).split(".")
        # A dotted quad of single digits is a version number, not an address.
        if all(len(part) == 1 for part in parts):
            continue
        octets = [int(part) for part in parts]
        if any(octet > 255 for octet in octets):
            continue
        spans.append((match.start(), match.end()))
    return spans


_DETECTORS = {
    "URL_TOKEN": lambda text: _regex_spans(_URL_TOKEN_RE, text),
    "API_KEY": lambda text: _regex_spans(_API_KEY_RE, text),
    "EMAIL": lambda text: _regex_spans(_EMAIL_RE, text),
    "PHONE": lambda text: _regex_spans(_PHONE_RE, text),
    "IP": _ip_spans,
}


def detect(text: str) -> list[EntitySpan]:
    """Every entity this text carries, ordered by position.

    Detectors overlap — a card number contains digit runs, an API key pattern
    can start inside another match. Overlapping matches merge into one span
    rather than being resolved by dropping the loser: dropping it would leave
    the characters it covered outside every placeholder and hand them to the
    model. The merged span takes the label of its widest member.
    """
    found: list[EntitySpan] = []
    for rank, entity_type in enumerate(_PRIORITY):
        if entity_type == "CARD":
            continue
        for start, end in _DETECTORS[entity_type](text):
            found.append(EntitySpan(start, end, entity_type, rank))
    card_rank = _PRIORITY.index("CARD")
    for start, end in _card_spans(text, found):
        found.append(EntitySpan(start, end, "CARD", card_rank))
    if not found:
        return []

    merged: list[EntitySpan] = []
    group: list[EntitySpan] = []
    for span in sorted(found, key=lambda s: (s.start, -s.end)):
        if group and span.start < max(member.end for member in group):
            group.append(span)
            continue
        if group:
            merged.append(_merge(group))
        group = [span]
    merged.append(_merge(group))
    return merged


def _merge(group: list[EntitySpan]) -> EntitySpan:
    """Collapse overlapping matches into the span that covers all of them."""
    widest = min(group, key=lambda s: (-(s.end - s.start), s.rank))
    return EntitySpan(
        start=min(member.start for member in group),
        end=max(member.end for member in group),
        entity_type=widest.entity_type,
        rank=widest.rank,
    )


def redact(text: str) -> RedactionResult:
    """Mask everything sensitive in ``text`` and report what was masked.

    Placeholders cannot be reversed, so this must always be given the original
    text.
    """
    spans = detect(text)
    chunks: list[str] = []
    cursor = 0
    counts: dict[str, int] = {}
    for span in spans:
        chunks.append(text[cursor : span.start])
        chunks.append(f"[{span.entity_type}]")
        counts[span.entity_type] = counts.get(span.entity_type, 0) + 1
        cursor = span.end
    chunks.append(text[cursor:])

    return RedactionResult(
        redacted_text="".join(chunks),
        entities_found=[
            DetectedEntitySummary(type=entity_type, count=counts[entity_type])
            for entity_type in _PRIORITY
            if entity_type in counts
        ],
        was_redacted=bool(counts),
    )


def redact_text(text: str) -> str:
    """Convenience wrapper returning only the redacted text."""
    return redact(text).redacted_text


def redact_for_egress(text: str | None) -> str:
    """Mask stored text on its way to the model.

    ``None``/empty input returns an empty string so callers can hand in
    nullable columns directly.
    """
    if not text:
        return ""
    return redact_text(text)
