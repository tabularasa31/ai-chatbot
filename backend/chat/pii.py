"""
PII Redaction Layer — Stage 1 (Regex).

Replaces sensitive entities with typed placeholders before
text is sent to any external API (OpenAI embeddings or completion).

Entities detected:
  Tier 1A (always redact):
    - EMAIL        → [EMAIL]
    - PHONE        → [PHONE]
    - API_KEY      → [API_KEY]
    - CREDIT_CARD  → [CREDIT_CARD]

Order of application: most-specific patterns first to avoid partial matches.
"""

import re

# ── Patterns ──────────────────────────────────────────────────────────────────

# API keys: Bearer tokens, sk-* (OpenAI style), hex strings 32+ chars, base64 tokens 40+ chars
_API_KEY_PATTERNS = [
    r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",  # Bearer <token>
    r"\bsk-[A-Za-z0-9]{20,}\b",  # OpenAI-style sk-...
    r"\b[A-Fa-f0-9]{32,}\b",  # hex 32+ chars (API keys, hashes)
    r"\b[A-Za-z0-9+/]{40,}={0,2}\b",  # base64 40+ chars
]

# Credit cards: 13–19 digit sequences with optional spaces/dashes
_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d[ -]?){13,18}\d\b"
)

# Phone numbers: RU formats + international
_PHONE_RE = re.compile(
    r"""
    (?:
        # International: +7, +1, +44, etc. with various separators
        \+\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{1,4}[\s\-.]?\d{1,9}
        |
        # RU: 8 (XXX) XXX-XX-XX or 8XXXXXXXXXX
        \b8[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{2}[\s\-.]?\d{2}\b
        |
        # Compact international without spaces: +79991234567
        \+\d{10,14}\b
    )
    """,
    re.VERBOSE,
)

# Email: standard RFC-like pattern
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Compile API key patterns
_API_KEY_RE = re.compile("|".join(_API_KEY_PATTERNS))


# ── Public API ─────────────────────────────────────────────────────────────────


def redact(text: str) -> tuple[str, bool]:
    """
    Redact PII from text using regex patterns.

    Applies patterns in order: API keys → credit cards → phones → emails.
    More specific patterns run first to avoid partial matches.

    Args:
        text: Original user message.

    Returns:
        Tuple of (redacted_text, was_redacted).
        was_redacted is True if any substitution was made.
    """
    original = text

    text = _API_KEY_RE.sub("[API_KEY]", text)
    text = _CREDIT_CARD_RE.sub("[CREDIT_CARD]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)

    return text, text != original


def redact_text(text: str) -> str:
    """Convenience wrapper — returns only the redacted string."""
    redacted, _ = redact(text)
    return redacted
