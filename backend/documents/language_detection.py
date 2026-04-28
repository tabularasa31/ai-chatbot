"""Detect a document's primary language at parse time.

The result is stored on ``Document.language`` and copied into each
``Embedding.metadata_json`` so retrieval can identify multi-language KBs
without re-sampling chunks at query time.
"""

from __future__ import annotations

from backend.chat.language import detect_language

# Cap input passed to langdetect — full document text can be megabytes, but
# language detection saturates quickly. A few KB is plenty for a reliable
# verdict and keeps the call cheap.
_DETECTION_INPUT_CAP = 4096


def detect_document_language(parsed_text: str | None) -> str | None:
    """Return an ISO 639-1 language code for the document, or None.

    Returns None when the text is empty or the detection is not reliable —
    storing None is safer than persisting a guess that would mislead
    cross-lingual retrieval.
    """
    if not parsed_text:
        return None
    sample = parsed_text[:_DETECTION_INPUT_CAP].strip()
    if not sample:
        return None
    detection = detect_language(sample)
    if not detection.is_reliable:
        return None
    code = (detection.detected_language or "").strip().lower()
    if not code or code == "unknown":
        return None
    return code
