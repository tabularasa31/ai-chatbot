"""Business logic for document upload, parsing, and document health linting."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.documents.constants import KNOWLEDGE_DOCUMENT_CAPACITY
from backend.documents.parsers import parse_markdown, parse_pdf, parse_swagger
from backend.gap_analyzer.repository import invalidate_bm25_cache_for_tenant
from backend.models import Document, DocumentStatus, DocumentType

_HEALTH_WARNING_TYPES = frozenset(
    {
        "empty_or_too_short",
        "parse_or_extraction_issue",
        "poor_structure",
        "incomplete_section",
        "low_information_density",
    }
)
_SEVERITY_PENALTY = {"high": 20, "medium": 10, "low": 5}
_WORD_RE = re.compile(r"\b[\w-]+\b", flags=re.UNICODE)
_PLACEHOLDER_RE = re.compile(
    r"\b(?:todo|tbd|coming soon|to be added|fill me|placeholder|заполнить позже|будет добавлено)\b",
    flags=re.IGNORECASE,
)
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+", flags=re.MULTILINE)
_PUNCTUATION_ENDINGS = (":", ",", ";", "(", "[", "{", "-", "—", "–", "/", "\\")  # noqa: RUF001
_POOR_STRUCTURE_HIGH_SECTION_WORDS = 700
_POOR_STRUCTURE_MEDIUM_SECTION_WORDS = 450
_POOR_STRUCTURE_LONG_DOC_WORDS = 900
_POOR_STRUCTURE_MIN_HEADINGS_FOR_LONG_DOC = 1
_LOW_INFORMATION_DENSITY_MIN_TOKENS = 120
_LOW_INFORMATION_DENSITY_MIN_UNIQUE_RATIO = 0.22
_LOW_INFORMATION_DENSITY_MIN_NON_EMPTY_LINES = 8
_LOW_INFORMATION_DENSITY_MAX_DUPLICATE_RATIO = 0.35
# These thresholds are intentionally conservative heuristics tuned for retrieval
# quality smoke checks: flag only clearly long, repetitive, or weakly structured
# content while avoiding noisy warnings on small documents.


def _iso_utc_z() -> str:
    return (
        dt.datetime.now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _compute_health_score(warnings: list[dict[str, Any]]) -> int:
    score = 100
    for w in warnings:
        sev = w.get("severity")
        if isinstance(sev, str):
            score -= _SEVERITY_PENALTY.get(sev, 0)
    return max(0, score)


def _normalize_warnings(raw: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        wtype = item.get("type")
        severity = item.get("severity")
        message = item.get("message")
        if (
            not isinstance(wtype, str)
            or wtype not in _HEALTH_WARNING_TYPES
            or not isinstance(severity, str)
            or severity not in _SEVERITY_PENALTY
            or not isinstance(message, str)
            or not message.strip()
        ):
            continue
        out.append({"type": wtype, "severity": severity, "message": message.strip()})
    return out


def _make_warning(wtype: str, severity: str, message: str) -> dict[str, str]:
    return {"type": wtype, "severity": severity, "message": message}


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _split_markdown_sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []

    for line in text.splitlines():
        if _MARKDOWN_HEADING_RE.match(line):
            if current:
                sections.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)

    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


def _detect_short_document(text: str) -> dict[str, str] | None:
    words = _word_count(text)
    if words == 0:
        return _make_warning(
            "empty_or_too_short",
            "high",
            "The document has no readable text after parsing.",
        )
    if words < 20:
        return _make_warning(
            "empty_or_too_short",
            "high",
            "The document is extremely short, which leaves too little content for reliable retrieval.",
        )
    if words < 80:
        return _make_warning(
            "empty_or_too_short",
            "medium",
            "The document is quite short and may not contain enough content to answer user questions reliably.",
        )
    return None


def _detect_parse_or_extraction_issue(text: str) -> dict[str, str] | None:
    if "\x00" in text or "\ufffd" in text:
        return _make_warning(
            "parse_or_extraction_issue",
            "high",
            "The parsed text contains replacement or null characters, which suggests extraction problems.",
        )

    visible_count = 0
    alnum_count = 0
    for ch in text:
        if ch.isspace():
            continue
        visible_count += 1
        if ch.isalnum():
            alnum_count += 1

    if visible_count < 200:
        return None
    alnum_ratio = alnum_count / visible_count
    if alnum_ratio < 0.55:
        return _make_warning(
            "parse_or_extraction_issue",
            "medium",
            "The parsed text looks noisy or symbol-heavy, which suggests extraction quality issues.",
        )
    return None


def _detect_poor_structure(text: str, file_type: DocumentType) -> dict[str, str] | None:
    if file_type != DocumentType.markdown:
        return None

    section_word_counts = [_word_count(section) for section in _split_markdown_sections(text)]
    if not section_word_counts:
        return None

    max_section_words = max(section_word_counts)
    heading_count = len(_MARKDOWN_HEADING_RE.findall(text))
    total_words = _word_count(text)

    if max_section_words >= _POOR_STRUCTURE_HIGH_SECTION_WORDS:
        return _make_warning(
            "poor_structure",
            "high",
            "At least one markdown section is very long without enough subheadings, which can hurt chunking and retrieval quality.",
        )
    if max_section_words >= _POOR_STRUCTURE_MEDIUM_SECTION_WORDS:
        return _make_warning(
            "poor_structure",
            "medium",
            "At least one markdown section is long without enough subheadings, which can make retrieval less precise.",
        )
    if (
        total_words >= _POOR_STRUCTURE_LONG_DOC_WORDS
        and heading_count <= _POOR_STRUCTURE_MIN_HEADINGS_FOR_LONG_DOC
    ):
        return _make_warning(
            "poor_structure",
            "medium",
            "The document is long but has almost no markdown headings, so different topics are likely mixed into the same chunks.",
        )
    return None


def _detect_incomplete_section(text: str) -> dict[str, str] | None:
    stripped = text.strip()
    if not stripped:
        return None

    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading_match = re.match(r"^\s*(#{1,6})\s+", line)
        if heading_match:
            current_level = len(heading_match.group(1))
            section_has_body = False
            for candidate in lines[index + 1 :]:
                stripped_candidate = candidate.strip()
                if not stripped_candidate:
                    continue
                next_heading_match = re.match(r"^\s*(#{1,6})\s+", candidate)
                if next_heading_match:
                    next_level = len(next_heading_match.group(1))
                    if next_level > current_level:
                        section_has_body = True
                    break
                section_has_body = True
                break
            if not section_has_body:
                return _make_warning(
                    "incomplete_section",
                    "medium",
                    "At least one heading has no body content underneath it, which makes the document look unfinished.",
                )

    if len(re.findall(r"^```", text, flags=re.MULTILINE)) % 2 == 1:
        return _make_warning(
            "incomplete_section",
            "medium",
            "A fenced code block is not properly closed, which suggests the document was cut off mid-section.",
        )

    if _PLACEHOLDER_RE.search(text):
        return _make_warning(
            "incomplete_section",
            "medium",
            "The document contains placeholder text such as TODO or coming soon, which suggests unfinished sections.",
        )

    tail = stripped[-160:]
    if tail.endswith(_PUNCTUATION_ENDINGS) or tail.endswith("..."):
        return _make_warning(
            "incomplete_section",
            "low",
            "The document appears to end mid-thought, which suggests the last section may be incomplete.",
        )
    return None


def _detect_low_information_density(text: str) -> dict[str, str] | None:
    tokens = [token.lower() for token in _WORD_RE.findall(text) if len(token) >= 4]
    if len(tokens) < _LOW_INFORMATION_DENSITY_MIN_TOKENS:
        return None

    unique_ratio = len(set(tokens)) / len(tokens)
    if unique_ratio < _LOW_INFORMATION_DENSITY_MIN_UNIQUE_RATIO:
        return _make_warning(
            "low_information_density",
            "medium",
            "The document repeats the same vocabulary heavily, which suggests low information density for retrieval.",
        )

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= _LOW_INFORMATION_DENSITY_MIN_NON_EMPTY_LINES:
        duplicate_ratio = 1 - (len(set(lines)) / len(lines))
        if duplicate_ratio >= _LOW_INFORMATION_DENSITY_MAX_DUPLICATE_RATIO:
            return _make_warning(
                "low_information_density",
                "low",
                "Large parts of the document are repeated verbatim, which reduces the amount of distinct information available for answers.",
            )
    return None


def _build_rule_based_warnings(text: str, file_type: DocumentType) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    detectors = (
        _detect_short_document,
        _detect_parse_or_extraction_issue,
        lambda value: _detect_poor_structure(value, file_type),
        _detect_incomplete_section,
        _detect_low_information_density,
    )
    for detector in detectors:
        warning = detector(text)
        if warning is not None:
            warnings.append(warning)
    return warnings


def run_document_health_check(
    document_id: uuid.UUID,
    db: Session,
) -> dict[str, Any]:
    """
    Run deterministic health lint checks on a document's parsed text.
    Updates document.health_status in DB.
    Returns the health_status dict.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        return {
            "score": None,
            "checked_at": _iso_utc_z(),
            "warnings": [],
            "error": "health check failed",
        }

    parsed_text = doc.parsed_text or ""
    if not parsed_text.strip():
        checked = _iso_utc_z()
        warnings = _normalize_warnings(
            _build_rule_based_warnings(parsed_text, doc.file_type)
        )
        result: dict[str, Any] = {
            "score": _compute_health_score(warnings),
            "checked_at": checked,
            "warnings": warnings,
        }
        doc.health_status = result
        db.commit()
        db.refresh(doc)
        return result

    try:
        warnings = _normalize_warnings(_build_rule_based_warnings(parsed_text, doc.file_type))
        checked = _iso_utc_z()
        result = {
            "score": _compute_health_score(warnings),
            "checked_at": checked,
            "warnings": warnings,
        }
        doc.health_status = result
        db.commit()
        db.refresh(doc)
        return result
    except Exception:
        checked = _iso_utc_z()
        err_result: dict[str, Any] = {
            "score": None,
            "checked_at": checked,
            "warnings": [],
            "error": "health check failed",
        }
        doc.health_status = err_result
        db.commit()
        db.refresh(doc)
        return err_result

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_TYPES = {"pdf", "markdown", "swagger"}


def _parse_content(content: bytes, file_type: str) -> str:
    """Parse content based on file_type. Raises ValueError on parse error."""
    if file_type == "pdf":
        return parse_pdf(content)
    if file_type == "markdown":
        return parse_markdown(content)
    if file_type == "swagger":
        return parse_swagger(content)
    raise ValueError(f"Unsupported file type: {file_type}")


def upload_document(
    tenant_id: uuid.UUID,
    filename: str,
    content: bytes,
    file_type: str,
    db: Session,
) -> Document:
    """
    Upload and parse a document.

    Validates file_type (pdf, markdown, swagger only) and size (max 50MB).
    Saves document with status=processing, parses, then updates to ready or error.
    Enforces a shared tenant-wide document capacity.
    """
    existing_count = db.query(Document).filter(Document.tenant_id == tenant_id).count()
    if existing_count >= KNOWLEDGE_DOCUMENT_CAPACITY:
        raise HTTPException(
            status_code=400,
            detail=f"Document limit reached (max {KNOWLEDGE_DOCUMENT_CAPACITY})",
        )

    if file_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: pdf, markdown, swagger",
        )
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum size is 50MB",
        )

    content_hash = hashlib.sha256(content).hexdigest()
    duplicate = (
        db.query(Document)
        .filter(
            Document.tenant_id == tenant_id,
            Document.content_hash == content_hash,
            Document.source_id.is_(None),
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"This file has already been uploaded (as '{duplicate.filename}'). Delete the existing document first to re-upload.",
        )

    doc_type = DocumentType[file_type]
    doc = Document(
        tenant_id=tenant_id,
        filename=filename,
        content_hash=content_hash,
        file_type=doc_type,
        status=DocumentStatus.processing,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        parsed = _parse_content(content, file_type)
        doc.parsed_text = parsed
        doc.status = DocumentStatus.ready
    except ValueError:
        doc.status = DocumentStatus.error
    db.commit()
    db.refresh(doc)
    return doc


def get_documents(tenant_id: uuid.UUID, db: Session) -> list[Document]:
    """Return all documents for this tenant, ordered by created_at DESC."""
    return (
        db.query(Document)
        .filter(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())
        .all()
    )


def get_document(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> Document:
    """
    Get document by id. Verifies ownership (document.tenant_id == tenant_id).
    Raises 404 if not found or not owner.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or doc.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def delete_document(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> None:
    """
    Delete document. Verifies ownership before delete.
    CASCADE: embeddings are deleted automatically (already in DB schema).
    Raises 404 if not found or not owner.
    """
    doc = get_document(document_id, tenant_id, db)
    db.delete(doc)
    db.commit()
    invalidate_bm25_cache_for_tenant(tenant_id)
