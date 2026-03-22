"""Business logic for document upload and parsing."""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.models import Document, DocumentStatus, DocumentType, Embedding
from backend.documents.parsers import parse_markdown, parse_pdf, parse_swagger

_HEALTH_WARNING_TYPES = frozenset(
    {
        "missing_contact_info",
        "poor_structure",
        "incomplete_sections",
        "no_examples",
        "outdated_content",
    }
)
_SEVERITY_PENALTY = {"high": 20, "medium": 10, "low": 5}
_APPROX_CHARS_PER_TOKEN = 4


def _iso_utc_z() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _truncate_to_approx_tokens(text: str, max_tokens: int = 3000) -> str:
    max_chars = max_tokens * _APPROX_CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


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


def _parse_health_json_from_content(content: str) -> dict[str, Any]:
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError("invalid health JSON")


def run_document_health_check(
    document_id: uuid.UUID,
    db: Session,
    api_key: str,
) -> dict[str, Any]:
    """
    Run GPT-based health check on a document's chunk texts.
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

    rows = (
        db.query(Embedding.chunk_text)
        .filter(Embedding.document_id == document_id)
        .order_by(Embedding.created_at.asc())
        .all()
    )
    parts = [r[0] for r in rows if r[0] and r[0].strip()]
    combined = "\n\n".join(parts)
    if not combined.strip() and doc.parsed_text:
        combined = doc.parsed_text

    excerpt = _truncate_to_approx_tokens(combined)
    if not excerpt.strip():
        checked = _iso_utc_z()
        result: dict[str, Any] = {
            "score": 100,
            "checked_at": checked,
            "warnings": [],
        }
        doc.health_status = result
        db.commit()
        db.refresh(doc)
        return result

    prompt = f"""Analyze this documentation excerpt and identify issues that could reduce the quality of AI-powered search and Q&A.

Return a JSON object with this exact structure:
{{
  "warnings": [
    {{"type": "<type>", "severity": "<low|medium|high>", "message": "<human-readable explanation>"}}
  ]
}}

Check for:
- missing_contact_info: No support email, phone, or contact section
- poor_structure: Long sections (500+ words) without subheadings
- incomplete_sections: Sections that appear cut off or unfinished
- no_examples: Important features described abstractly with no examples
- outdated_content: References to specific old dates or deprecated versions

Only report issues that are clearly present. Return empty warnings array if the document looks good.
Return ONLY the JSON, no other text.

Documentation excerpt:
{excerpt}
"""

    try:
        openai_client = get_openai_client(api_key)
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw_content = response.choices[0].message.content or ""
        parsed = _parse_health_json_from_content(raw_content)
        if "warnings" not in parsed:
            raw_warnings: list[Any] = []
        else:
            raw_warnings = parsed["warnings"]
            if not isinstance(raw_warnings, list):
                raise ValueError("warnings must be a list")
        warnings = _normalize_warnings(raw_warnings)
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
    client_id: uuid.UUID,
    filename: str,
    content: bytes,
    file_type: str,
    db: Session,
) -> Document:
    """
    Upload and parse a document.

    Validates file_type (pdf, markdown, swagger only) and size (max 50MB).
    Saves document with status=processing, parses, then updates to ready or error.
    Enforces max 20 documents per client.
    """
    existing_count = db.query(Document).filter(Document.client_id == client_id).count()
    if existing_count >= 20:
        raise HTTPException(
            status_code=400,
            detail="Document limit reached (max 20)",
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

    doc_type = DocumentType[file_type]
    doc = Document(
        client_id=client_id,
        filename=filename,
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


def get_documents(client_id: uuid.UUID, db: Session) -> list[Document]:
    """Return all documents for this client, ordered by created_at DESC."""
    return (
        db.query(Document)
        .filter(Document.client_id == client_id)
        .order_by(Document.created_at.desc())
        .all()
    )


def get_document(
    document_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> Document:
    """
    Get document by id. Verifies ownership (document.client_id == client_id).
    Raises 404 if not found or not owner.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or doc.client_id != client_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def delete_document(
    document_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> None:
    """
    Delete document. Verifies ownership before delete.
    CASCADE: embeddings are deleted automatically (already in DB schema).
    Raises 404 if not found or not owner.
    """
    doc = get_document(document_id, client_id, db)
    db.delete(doc)
    db.commit()
