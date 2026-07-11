"""Stream filters and answer-text post-processing for the generation step.

Every class here wraps the downstream SSE emit callback and filters the token
stream in real time; the module-level helpers do the equivalent cleanup on
assembled (non-streamed) answer text.

Test seam note: :class:`LanguageGateStreamFilter` resolves ``detect_language``
through ``backend.chat.handlers.rag`` module globals at call time, so existing
``monkeypatch.setattr("backend.chat.handlers.rag.detect_language", ...)``
continues to intercept it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from backend.chat.language import LangDetectError, _language_root

logger = logging.getLogger(__name__)

_INLINE_CITATION_RE = re.compile(
    r"\s*\((?:Page|Section):[^()]*(?:\([^()]*\)[^()]*)*\)",
    re.IGNORECASE,
)

# Matches any partial prefix of "(Page:..." or "(Section:..." at the end of a
# string (no closing ")") — used by _CitationStreamFilter to detect incomplete
# citations that span multiple streamed tokens.
_CITATION_TAIL_RE = re.compile(
    r"\((?:P(?:a(?:g(?:e(?::[^)]*)?)?)?)?|S(?:e(?:c(?:t(?:i(?:o(?:n(?::[^)]*)?)?)?)?)?)?)?)?$",
    re.IGNORECASE,
)


def _strip_inline_citations(text: str) -> str:
    """Remove (Page: ...) and (Section: ...) annotations the LLM may echo back."""
    return _INLINE_CITATION_RE.sub("", text).strip()


class _CitationStreamFilter:
    """Wraps a stream_callback and strips inline citations from streamed tokens.

    Citations like ``(Page: FAQ)`` often span many tokens. This class buffers
    the incoming stream, strips complete patterns with ``_INLINE_CITATION_RE``,
    and holds back any partial citation prefix at the tail until the closing
    ``)`` arrives or the stream ends.
    """

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._cb = callback
        self._buf = ""

    def feed(self, chunk: str) -> None:
        self._buf += chunk
        self._buf = _INLINE_CITATION_RE.sub("", self._buf)
        m = _CITATION_TAIL_RE.search(self._buf)
        if m:
            safe = self._buf[: m.start()].rstrip(" \t")
            self._buf = self._buf[m.start() :]
        else:
            safe = self._buf
            self._buf = ""
        if safe:
            self._cb(safe)

    def finish(self) -> None:
        if self._buf:
            cleaned = _INLINE_CITATION_RE.sub("", self._buf).strip()
            if cleaned:
                self._cb(cleaned)
            self._buf = ""


def _thought_truncated(text: str) -> bool:
    """True when a <thought> block was cut off before its closing tag
    (max_tokens ran out mid-CoT). Shared by the stripping warning below and
    the generation step's Langfuse ``thought_truncated`` metadata so the two
    can never disagree."""
    return "<thought>" in text and "</thought>" not in text


def _strip_thought_tags(text: str) -> str:
    """Remove <thought>...</thought> blocks the model may emit for CoT reasoning.

    Handles truncated responses where max_tokens cut off before </thought>.
    """
    if _thought_truncated(text):
        logger.warning(
            "thought_tag_truncated: <thought> without closing tag — max_tokens likely cut off CoT block"
        )
    return re.sub(r"<thought>.*?(?:</thought>|\Z)\s*", "", text, flags=re.DOTALL).strip()


# Sentinel the LLM appends when it ends its reply with an offer to open a
# support ticket. Detecting this lets us arm escalation_pre_confirm_pending
# in any language without natural-language pattern matching — the marker is
# machine-emitted and stripped before the reply reaches the user.
OFFER_MARKER = "<offered_ticket/>"


_OFFER_MARKER_TERMINAL_RE = re.compile(
    re.escape(OFFER_MARKER) + r"[\s\.,!?;:\"'»)\]]*\Z"
)


def _strip_and_detect_offer_marker(text: str) -> tuple[str, bool]:
    """Return (text with terminal OFFER_MARKER removed, True if it was the suffix).

    The prompt contract says the marker is appended as the very last token of
    the reply. In practice the LLM often appends an extra period, quote, or
    whitespace right after the marker (a common LLM tic when the sentinel
    gets templated into a sentence). We tolerate any trailing punctuation /
    whitespace, but a marker followed by substantive text is treated as
    mid-text and ignored — to avoid two failure modes:
      * silently rewriting legitimate content that happens to contain the
        literal string;
      * false-arming escalation_pre_confirm_pending on the next user turn.

    Defensive UX cleanup of mid-text occurrences (so the literal never reaches
    the UI even when the LLM misplaces it) happens separately at the call
    site, by ``replace`` on the returned text. Detection itself stays strict.
    """
    if not text:
        return text, False
    match = _OFFER_MARKER_TERMINAL_RE.search(text)
    if not match:
        return text, False
    cleaned = text[: match.start()].rstrip()
    return cleaned, True


def _scrub_offer_marker_literal(text: str) -> str:
    """Belt-and-suspenders strip of any remaining OFFER_MARKER occurrences.

    Used after detection on assembled (non-streamed) answer text so a marker
    the LLM mis-emitted mid-reply cannot leak to the user even though it
    didn't arm pre_confirm. The streaming path has its own filter
    (OfferMarkerStreamFilter) that does the equivalent for SSE chunks.
    """
    if not text or OFFER_MARKER not in text:
        return text
    return text.replace(OFFER_MARKER, "")


class OfferMarkerStreamFilter:
    """Strip ``OFFER_MARKER`` from a streamed SSE token sequence (defensive UX).

    Wraps the downstream emit callback and buffers up to ``len(OFFER_MARKER)-1``
    trailing chars so a marker arriving across two SSE chunks is never partially
    emitted to the user. Removes every occurrence (not only the terminal one)
    so a hallucinated or echoed literal in mid-reply never reaches the UI.

    The filter does NOT decide whether the reply was a ticket offer — that
    decision is terminal-only and lives in :func:`_strip_and_detect_offer_marker`,
    which runs on the assembled raw text after the stream completes. This
    separation prevents a mid-text literal from false-arming the pre-confirm
    gate while still keeping the UI clean.
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._buf = ""

    def feed(self, text: str) -> None:
        self._buf += text
        while True:
            idx = self._buf.find(OFFER_MARKER)
            if idx >= 0:
                if idx > 0:
                    self._emit(self._buf[:idx])
                self._buf = self._buf[idx + len(OFFER_MARKER):]
                continue
            # Preserve a possible split-boundary suffix so the marker isn't
            # partially leaked when it straddles two chunks.
            safe_end = len(self._buf)
            for prefix_len in range(min(len(OFFER_MARKER) - 1, len(self._buf)), 0, -1):
                if self._buf[-prefix_len:] == OFFER_MARKER[:prefix_len]:
                    safe_end = len(self._buf) - prefix_len
                    break
            if safe_end > 0:
                self._emit(self._buf[:safe_end])
            self._buf = self._buf[safe_end:]
            break

    def flush_end(self) -> None:
        # Leftover possibilities:
        #   * Exact full marker → drop (detected, never emit).
        #   * A non-empty *prefix* of the marker (split-boundary suffix that
        #     `feed` was holding back in case the rest arrived) → drop. This
        #     covers truncated streams (max_completion_tokens, client
        #     disconnect, OpenAI 5xx mid-stream) where the rest of the marker
        #     will never arrive; emitting the partial would leak '<offered_tic'
        #     to the user.
        #   * Anything else → real content, emit it.
        if not self._buf:
            return
        if self._buf == OFFER_MARKER or OFFER_MARKER.startswith(self._buf):
            self._buf = ""
            return
        self._emit(self._buf)
        self._buf = ""


class ThoughtStreamFilter:
    """Filter <thought>...</thought> blocks from an SSE text stream in real time.

    Feed each incoming delta via feed(); call flush_end() after the last chunk.
    The emit callback receives only text that should reach the user.

    Handles tags split across chunk boundaries and multiple consecutive thought blocks.
    Unclosed <thought> at end of stream is silently discarded.
    """

    _OPEN_TAG = "<thought>"
    _CLOSE_TAG = "</thought>"

    def __init__(
        self,
        emit: Callable[[str], None],
        on_phase_change: Callable[[str], None] | None = None,
    ) -> None:
        self._emit = emit
        self._on_phase_change = on_phase_change
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> None:
        self._buf += text
        self._process()

    def flush_end(self) -> None:
        if not self._inside and self._buf:
            self._emit(self._buf)
        self._buf = ""
        self._inside = False

    def _notify_phase(self, phase: str) -> None:
        if self._on_phase_change is None:
            return
        try:
            self._on_phase_change(phase)
        except Exception:
            logger.debug(
                "ThoughtStreamFilter phase callback failed for phase: %s",
                phase,
                exc_info=True,
            )

    def _process(self) -> None:
        while self._buf:
            tag = self._CLOSE_TAG if self._inside else self._OPEN_TAG
            idx = self._buf.find(tag)
            if idx >= 0:
                if not self._inside and idx > 0:
                    self._emit(self._buf[:idx])
                self._buf = self._buf[idx + len(tag):]
                self._inside = not self._inside
                self._notify_phase("reasoning" if self._inside else "writing")
            else:
                # No complete tag found; keep a potential split-boundary prefix in the
                # buffer so a tag arriving across two chunks is handled correctly.
                safe_end = len(self._buf)
                for prefix_len in range(min(len(tag) - 1, len(self._buf)), 0, -1):
                    if self._buf[-prefix_len:] == tag[:prefix_len]:
                        safe_end = len(self._buf) - prefix_len
                        break
                if not self._inside and safe_end > 0:
                    self._emit(self._buf[:safe_end])
                self._buf = self._buf[safe_end:]
                break


class LanguageMismatchStreamAbortError(Exception):
    """Raised by :class:`LanguageGateStreamFilter` when the streamed answer is
    reliably detected in a language other than the expected one before any
    text has been forwarded to the client.

    The caller catches this and regenerates once with the expected language
    forced, streaming the retry to the client instead — the user never sees
    the wrong-language attempt, and the wrong-language generation is aborted
    early instead of running to completion.
    """

    def __init__(self, detected_language: str) -> None:
        super().__init__(f"streamed answer language mismatch: {detected_language}")
        self.detected_language = detected_language


class LanguageGateStreamFilter:
    """Hold back the head of a streamed answer until its language is verified.

    Sits between the marker/thought filters and the real SSE emit callback.
    Buffers user-visible text until ``min_chars`` accumulate, runs
    ``detect_language`` on the buffered head once, then either flushes and
    becomes a transparent passthrough (language matches, or detection is
    unreliable — fail open) or raises :exc:`LanguageMismatchStreamAbortError`
    before a single character reaches the client.

    ``flush_end`` runs the same check for answers shorter than ``min_chars``;
    short texts usually fail the ``is_reliable`` bar and fail open.
    """

    def __init__(
        self,
        emit: Callable[[str], None],
        *,
        expected_language: str,
        min_chars: int = 80,
    ) -> None:
        self._emit = emit
        self._expected_root = _language_root(expected_language)
        self._min_chars = min_chars
        self._buf: list[str] = []
        self._buffered_len = 0
        self._passthrough = False

    def feed(self, text: str) -> None:
        if self._passthrough:
            self._emit(text)
            return
        self._buf.append(text)
        self._buffered_len += len(text)
        if self._buffered_len >= self._min_chars:
            self._check_and_flush()

    def flush_end(self) -> None:
        if not self._passthrough and self._buf:
            self._check_and_flush()

    def _check_and_flush(self) -> None:
        # Resolved via the rag module so test monkeypatches on
        # ``backend.chat.handlers.rag.detect_language`` keep intercepting it.
        from backend.chat.handlers import rag as _rag

        head = "".join(self._buf)
        try:
            detection = _rag.detect_language(head)
        except LangDetectError:
            detection = None
        if (
            detection is not None
            and detection.is_reliable
            and detection.detected_language != "unknown"
            and _language_root(detection.detected_language) != self._expected_root
        ):
            raise LanguageMismatchStreamAbortError(detection.detected_language)
        self._passthrough = True
        self._buf = []
        self._buffered_len = 0
        self._emit(head)
