from __future__ import annotations

import re

from backend.tenant_knowledge.schemas import AliasEntry, GlossaryEntry


def extract_openapi_knowledge(
    *,
    swagger_text: str,
) -> tuple[list[str], list[GlossaryEntry], list[AliasEntry]]:
    """
    Lightweight extraction from the stringified Swagger text produced by `parse_swagger`.

    Note: this repo stores only `Document.parsed_text`, not the original OpenAPI JSON;
    therefore extraction is done via regex over those lines.
    """
    topics: list[str] = []
    seen_topics: set[str] = set()

    for match in re.finditer(r"^\s*(?:Tags|Tag):\s*(.+?)\s*$", swagger_text, flags=re.IGNORECASE | re.MULTILINE):
        raw = match.group(1).strip()
        # split by comma, keep order
        for part in [p.strip() for p in re.split(r",\s*", raw) if p.strip()]:
            key = part.casefold()
            if key in seen_topics:
                continue
            seen_topics.add(key)
            topics.append(part)

    glossary_terms: list[GlossaryEntry] = []

    # Endpoint blocks: "Endpoint: GET /path" + following "Description: ..."
    endpoint_pattern = (
        r"^\s*Endpoint:\s*(?P<method>[A-Z]+)\s+(?P<path>/[^\s]+)\s*$"
        r"\n\s*Description:\s*(?P<desc>.*?)\s*$"
        r"(?=\n\s*(?:OperationId:|Parameters:|ErrorCode:|Error:|Endpoint:|Tags:|API:)|\Z)"
    )
    for m in re.finditer(endpoint_pattern, swagger_text, flags=re.IGNORECASE | re.MULTILINE):
        path = m.group("path").strip()
        if not path:
            continue
        desc = m.group("desc").strip()
        glossary_terms.append(
            GlossaryEntry(
                term=path,
                definition=desc if desc else None,
                confidence=0.9,
                source="swagger",
            )
        )

    # OperationId → aliases
    aliases: list[AliasEntry] = []
    op_id_seen: set[str] = set()
    for m in re.finditer(r"^\s*OperationId:\s*(.+?)\s*$", swagger_text, flags=re.MULTILINE):
        op_id = m.group(1).strip()
        if not op_id:
            continue
        key = op_id.casefold()
        if key in op_id_seen:
            continue
        op_id_seen.add(key)
        aliases.append(
            AliasEntry(
                user_phrase=op_id,
                canonical_term=op_id,
                confidence=0.9,
            )
        )

    # Error codes (optional): "ErrorCode: <code> - <description>"
    for m in re.finditer(
        r"^\s*(?:ErrorCode|Error):\s*(?P<term>[A-Za-z0-9_.-]+)(?:\s*[-:]\s*(?P<def>.+))?$",
        swagger_text,
        flags=re.MULTILINE,
    ):
        term = m.group("term").strip()
        if not term:
            continue
        definition = m.group("def").strip() if m.group("def") else None
        glossary_terms.append(
            GlossaryEntry(
                term=term,
                definition=definition,
                confidence=0.9,
                source="swagger",
            )
        )

    return topics, glossary_terms, aliases

