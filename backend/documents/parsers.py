"""Document parsers for PDF, Markdown, and Swagger/OpenAPI."""

from __future__ import annotations

import json
from typing import Any

import yaml
from pypdf import PdfReader
from io import BytesIO


def parse_pdf(content: bytes) -> str:
    """
    Extract text from PDF using pypdf.

    Returns extracted text as single string.
    Raises ValueError if file is corrupted/unreadable.
    """
    try:
        reader = PdfReader(BytesIO(content))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
        return "\n\n".join(parts) if parts else ""
    except Exception as e:
        raise ValueError(f"PDF is corrupted or unreadable: {e}") from e


def parse_markdown(content: bytes) -> str:
    """
    Decode markdown bytes to UTF-8 and return raw text.

    Markdown syntax is preserved.
    """
    return content.decode("utf-8")


def parse_swagger(content: bytes) -> str:
    """
    Parse OpenAPI/Swagger JSON or YAML to readable text.

    Tries JSON first, falls back to YAML.
    Extracts paths, descriptions, parameters.
    Raises ValueError if not valid JSON/YAML.
    """
    text = content.decode("utf-8")
    data: dict[str, Any]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"Not valid JSON or YAML: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("Swagger content must be a JSON object")

    info = data.get("info", {}) or {}
    title = info.get("title", "Unknown API")
    paths = data.get("paths", {}) or {}
    tags = data.get("tags", []) or []
    tag_names: list[str] = []
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, dict):
                name = t.get("name")
                if isinstance(name, str) and name.strip():
                    tag_names.append(name.strip())
            elif isinstance(t, str) and t.strip():
                tag_names.append(t.strip())

    lines: list[str] = [f"API: {title}"]
    if tag_names:
        lines.append(f"Tags: {', '.join(tag_names)}")

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            if method.startswith("/") or not isinstance(spec, dict):
                continue
            desc = spec.get("description", spec.get("summary", ""))
            params = spec.get("parameters", [])
            param_names = [
                p.get("name", "")
                for p in params
                if isinstance(p, dict) and "name" in p
            ]
            params_str = ", ".join(param_names) if param_names else "none"
            lines.append(f"\nEndpoint: {method.upper()} {path}")
            lines.append(f"  Description: {desc}")
            op_id = spec.get("operationId")
            if isinstance(op_id, str) and op_id.strip():
                lines.append(f"  OperationId: {op_id.strip()}")
            lines.append(f"  Parameters: {params_str}")

            responses = spec.get("responses", {}) or {}
            if isinstance(responses, dict):
                for code, r_spec in responses.items():
                    if not isinstance(code, str) or not (code.startswith("4") or code.startswith("5")):
                        continue
                    if not isinstance(r_spec, dict):
                        continue
                    r_desc = r_spec.get("description")
                    if isinstance(r_desc, str) and r_desc.strip():
                        lines.append(f"  ErrorCode: {code} - {r_desc.strip()}")

    return "\n".join(lines) if lines else f"API: {title}"
