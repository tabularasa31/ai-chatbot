"""Document parsers for PDF, Markdown, and Swagger/OpenAPI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import yaml
from pypdf import PdfReader

OPENAPI_OPERATION_SEPARATOR = "\n\n<<<OPENAPI_OPERATION>>>\n\n"
OPENAPI_OPERATION_START_MARKER = "\n\n<<<OPENAPI_OPERATION_START>>>\n"
OPENAPI_OPERATION_END_MARKER = "\n\n<<<OPENAPI_OPERATION_END>>>\n"
OPENAPI_CHUNK_META_PREFIX = "OpenAPIChunkMeta: "
OPENAPI_REQUEST_DETAIL_MARKER = "\nRequest Schema Detail:\n"
OPENAPI_RESPONSE_DETAIL_MARKER = "\nResponse Schema Detail:\n"
_SUPPORTED_HTTP_METHODS = (
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
)
_MAX_SCHEMA_DEPTH = 4
_MAX_SCHEMA_FIELDS = 8
_MAX_EXTENSION_ITEMS = 5
_MAX_TEXT_ITEMS = 6
_RICH_SCHEMA_PROPERTY_THRESHOLD = 4
_NESTED_SCHEMA_DETAIL_LIMIT = 4


@dataclass
class OpenAPIChunk:
    text: str
    path: str
    method: str
    operation_id: str | None
    tags: list[str]
    deprecated: bool
    content_types: list[str]
    response_codes: list[str]
    auth_schemes: list[str]
    has_examples: bool
    source_format: str
    spec_version: str


def parse_pdf(content: bytes) -> str:
    """Extract text from PDF using pypdf."""
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
    """Decode markdown bytes to UTF-8 and return raw text."""
    return content.decode("utf-8")


def parse_swagger(content: bytes) -> str:
    """Parse OpenAPI/Swagger JSON or YAML and return a deterministic preview."""
    preview, _, _, _ = build_openapi_ingestion_payload(content)
    return preview


def load_openapi_spec(content: bytes) -> tuple[dict[str, Any], str]:
    """Load JSON/YAML bytes into an object and report the detected source format."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"Not valid UTF-8 text: {e}") from e
    return load_openapi_spec_text(text)


def load_openapi_spec_text(text: str) -> tuple[dict[str, Any], str]:
    """Load JSON/YAML text into an object and report the detected source format."""
    parsed: Any
    try:
        parsed = json.loads(text)
        source_format = "json"
    except json.JSONDecodeError:
        try:
            parsed = yaml.safe_load(text)
            source_format = "yaml"
        except yaml.YAMLError as e:
            raise ValueError(f"Not valid JSON or YAML: {e}") from e

    if not isinstance(parsed, dict):
        raise ValueError("OpenAPI content must be a JSON/YAML object")
    return parsed, source_format


def looks_like_openapi(spec: dict[str, Any]) -> bool:
    """Minimal heuristic for routing OpenAPI-like inputs."""
    return any(key in spec for key in ("openapi", "swagger", "paths"))


def build_openapi_ingestion_payload(content: bytes) -> tuple[str, list[OpenAPIChunk], str, str]:
    """Return preview text plus retrieval chunks for a valid OpenAPI spec."""
    spec, source_format = load_openapi_spec(content)
    return build_openapi_ingestion_payload_from_spec(spec, source_format)


def build_openapi_ingestion_payload_from_spec(
    spec: dict[str, Any], source_format: str
) -> tuple[str, list[OpenAPIChunk], str, str]:
    """Return preview text plus retrieval chunks for a validated OpenAPI spec object."""
    spec_version = _validate_openapi_spec(spec)
    title = _string_or_none((spec.get("info") or {}).get("title")) or "Unknown API"
    chunks = _build_openapi_chunks(spec, source_format=source_format, spec_version=spec_version)
    if not chunks:
        raise ValueError("OpenAPI spec must contain at least one supported operation")

    header_lines = [
        f"API: {title}",
        f"SpecVersion: {spec_version}",
        f"SourceFormat: {source_format}",
        f"OperationCount: {len(chunks)}",
    ]
    preview = "\n".join(header_lines) + "".join(_serialize_openapi_chunk_block(chunk) for chunk in chunks)
    return preview, chunks, source_format, spec_version


def extract_openapi_chunks_from_rendered_text(text: str) -> tuple[list[OpenAPIChunk], str | None, str | None]:
    """Rehydrate OpenAPI chunks from deterministic preview text."""
    header_text, block_texts = _split_rendered_openapi_blocks(text)
    if not block_texts:
        return [], None, None
    header = _parse_header_lines(header_text)
    source_format = header.get("SourceFormat")
    spec_version = header.get("SpecVersion")
    if not source_format or not spec_version:
        return [], source_format, spec_version

    chunks: list[OpenAPIChunk] = []
    for block in block_texts:
        chunk = _deserialize_openapi_chunk_block(
            block,
            source_format=source_format,
            spec_version=spec_version,
        )
        if chunk is None:
            continue
        chunks.append(chunk)
    return chunks, source_format, spec_version


def _parse_header_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _split_rendered_openapi_blocks(text: str) -> tuple[str, list[str]]:
    if OPENAPI_OPERATION_START_MARKER in text:
        header_text, _, remainder = text.partition(OPENAPI_OPERATION_START_MARKER)
        blocks = [
            block
            for block in remainder.split(OPENAPI_OPERATION_START_MARKER)
            if block.strip()
        ]
        return header_text, blocks
    if OPENAPI_OPERATION_SEPARATOR not in text:
        return text, []
    header_text, *legacy_blocks = text.split(OPENAPI_OPERATION_SEPARATOR)
    return header_text, legacy_blocks


def _serialize_openapi_chunk_block(chunk: OpenAPIChunk) -> str:
    meta = json.dumps(
        {
            "path": chunk.path,
            "method": chunk.method,
            "operation_id": chunk.operation_id,
            "tags": chunk.tags,
            "deprecated": chunk.deprecated,
            "content_types": chunk.content_types,
            "response_codes": chunk.response_codes,
            "auth_schemes": chunk.auth_schemes,
            "has_examples": chunk.has_examples,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        f"{OPENAPI_OPERATION_START_MARKER}"
        f"{OPENAPI_CHUNK_META_PREFIX}{meta}\n"
        f"{chunk.text.strip()}\n"
        f"{OPENAPI_OPERATION_END_MARKER}"
    )


def _deserialize_openapi_chunk_block(
    block: str,
    *,
    source_format: str,
    spec_version: str,
) -> OpenAPIChunk | None:
    normalized = block.strip()
    if not normalized:
        return None

    if normalized.startswith(OPENAPI_CHUNK_META_PREFIX):
        meta_line, _, body = normalized.partition("\n")
        try:
            raw_meta = json.loads(meta_line.removeprefix(OPENAPI_CHUNK_META_PREFIX).strip())
        except json.JSONDecodeError:
            raw_meta = None
        if isinstance(raw_meta, dict):
            text = body.removesuffix(OPENAPI_OPERATION_END_MARKER.strip()).strip()
            endpoint_chunk = _parse_rendered_openapi_block(
                text,
                source_format=source_format,
                spec_version=spec_version,
            )
            if endpoint_chunk is None:
                return None
            return OpenAPIChunk(
                text=text,
                path=str(raw_meta.get("path") or endpoint_chunk.path),
                method=str(raw_meta.get("method") or endpoint_chunk.method).lower(),
                operation_id=_string_or_none(raw_meta.get("operation_id")) or endpoint_chunk.operation_id,
                tags=(
                    _normalize_string_list(raw_meta.get("tags"))
                    if raw_meta.get("tags") is not None
                    else endpoint_chunk.tags
                ),
                deprecated=bool(raw_meta.get("deprecated", endpoint_chunk.deprecated)),
                content_types=(
                    _normalize_string_list(raw_meta.get("content_types"))
                    if raw_meta.get("content_types") is not None
                    else endpoint_chunk.content_types
                ),
                response_codes=(
                    _normalize_string_list(raw_meta.get("response_codes"))
                    if raw_meta.get("response_codes") is not None
                    else endpoint_chunk.response_codes
                ),
                auth_schemes=(
                    _normalize_string_list(raw_meta.get("auth_schemes"))
                    if raw_meta.get("auth_schemes") is not None
                    else endpoint_chunk.auth_schemes
                ),
                has_examples=bool(raw_meta.get("has_examples", endpoint_chunk.has_examples)),
                source_format=source_format,
                spec_version=spec_version,
            )

    return _parse_rendered_openapi_block(
        normalized.removesuffix(OPENAPI_OPERATION_END_MARKER.strip()).strip(),
        source_format=source_format,
        spec_version=spec_version,
    )


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _validate_openapi_spec(spec: dict[str, Any]) -> str:
    spec_version = _string_or_none(spec.get("openapi")) or _string_or_none(spec.get("swagger"))
    if not spec_version:
        raise ValueError("Object is not an OpenAPI/Swagger spec: missing openapi/swagger version")

    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI spec must contain a paths object")

    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for key, operation in path_item.items():
            if key.lower() in _SUPPORTED_HTTP_METHODS and isinstance(operation, dict):
                return spec_version

    raise ValueError("OpenAPI spec must contain at least one supported operation in paths")


def _build_openapi_chunks(
    spec: dict[str, Any], *, source_format: str, spec_version: str
) -> list[OpenAPIChunk]:
    top_level_security = spec.get("security")
    chunks: list[OpenAPIChunk] = []
    for path, method, operation, path_item in _iter_operations(spec):
        params = _merge_parameters(spec, path_item, operation)
        request_lines, request_detail_lines, request_content_types, request_has_examples = _render_request_body(
            spec, operation, params
        )
        response_lines, response_detail_lines, response_codes, response_content_types, response_has_examples = _render_responses(
            spec, operation
        )
        auth_schemes = _resolve_security_names(
            spec,
            operation.get("security") if "security" in operation else top_level_security,
        )
        has_examples = request_has_examples or response_has_examples
        example_line = _build_example_call(path, method, auth_schemes, params)
        if example_line:
            has_examples = True

        tags = _clean_string_list(operation.get("tags"))
        deprecated = bool(operation.get("deprecated", False))
        op_id = _string_or_none(operation.get("operationId"))

        lines = [f"Endpoint: {method.upper()} {path}"]
        if op_id:
            lines.append(f"Operation ID: {op_id}")

        summary = _string_or_none(operation.get("summary"))
        if summary:
            lines.append(f"Summary: {summary}")

        description = _string_or_none(operation.get("description"))
        if description:
            lines.append(f"Description: {description}")

        if tags:
            lines.append(f"Tags: {', '.join(tags)}")
        if deprecated:
            lines.append("Status: Deprecated")
        if auth_schemes:
            lines.append(f"Authentication: {', '.join(auth_schemes)}")

        param_lines = _render_parameters(spec, params)
        if param_lines:
            lines.append("Parameters:")
            lines.extend(param_lines)
        if request_lines:
            lines.append("Request Body:")
            lines.extend(request_lines)
        if response_lines:
            lines.append("Responses:")
            lines.extend(response_lines)
        if example_line:
            lines.append("Example Call:")
            lines.append(example_line)

        extension_lines = _render_extensions(operation)
        if extension_lines:
            lines.append("Extensions:")
            lines.extend(extension_lines)
        if request_detail_lines:
            lines.append("Request Schema Detail:")
            lines.extend(request_detail_lines)
        if response_detail_lines:
            lines.append("Response Schema Detail:")
            lines.extend(response_detail_lines)

        chunks.append(
            OpenAPIChunk(
                text="\n".join(lines),
                path=path,
                method=method,
                operation_id=op_id,
                tags=tags,
                deprecated=deprecated,
                content_types=_dedupe_preserve_order(request_content_types + response_content_types),
                response_codes=response_codes,
                auth_schemes=auth_schemes,
                has_examples=has_examples,
                source_format=source_format,
                spec_version=spec_version,
            )
        )
    return chunks


def _iter_operations(
    spec: dict[str, Any]
):
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return

    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method in _SUPPORTED_HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict):
                yield path, method, operation, path_item


def _merge_parameters(
    spec: dict[str, Any], path_item: dict[str, Any], operation: dict[str, Any]
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in path_item.get("parameters", []) or []:
        param = _resolve_local_value(spec, raw)
        if not isinstance(param, dict):
            continue
        name = _string_or_none(param.get("name"))
        location = _string_or_none(param.get("in"))
        if not name or not location:
            continue
        merged[(name, location)] = param
    for raw in operation.get("parameters", []) or []:
        param = _resolve_local_value(spec, raw)
        if not isinstance(param, dict):
            continue
        name = _string_or_none(param.get("name"))
        location = _string_or_none(param.get("in"))
        if not name or not location:
            continue
        merged[(name, location)] = param
    return list(merged.values())


def _render_parameters(spec: dict[str, Any], params: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in ("path", "query", "header", "cookie")}
    for param in params:
        location = _string_or_none(param.get("in"))
        if location in grouped and location != "body":
            grouped[location].append(param)

    for location in ("path", "query", "header", "cookie"):
        for param in grouped[location]:
            name = _string_or_none(param.get("name")) or "unknown"
            required = bool(param.get("required", False))
            description = _string_or_none(param.get("description"))
            if "schema" in param:
                schema_hint = _summarize_schema(spec, param.get("schema"))
            else:
                schema_hint = _string_or_none(param.get("type")) or "object"
            suffix = f": {description}" if description else ""
            lines.append(
                f"- {location} {name} ({'required' if required else 'optional'}, {schema_hint}){suffix}"
            )
    return lines


def _render_request_body(
    spec: dict[str, Any], operation: dict[str, Any], params: list[dict[str, Any]]
) -> tuple[list[str], list[str], list[str], bool]:
    lines: list[str] = []
    detail_lines: list[str] = []
    content_types: list[str] = []
    has_examples = False

    request_body = operation.get("requestBody")
    if request_body is not None:
        resolved = _resolve_local_value(spec, request_body)
        if isinstance(resolved, dict):
            required = bool(resolved.get("required", False))
            content = resolved.get("content")
            if isinstance(content, dict):
                for content_type, media in content.items():
                    if not isinstance(content_type, str):
                        continue
                    content_types.append(content_type)
                    media_dict = media if isinstance(media, dict) else {}
                    schema_hint = _summarize_schema(spec, media_dict.get("schema"))
                    example_hint = _extract_example_hint(spec, media_dict)
                    if example_hint:
                        has_examples = True
                    line = f"- {content_type} ({'required' if required else 'optional'}): {schema_hint}"
                    if example_hint:
                        line += f"; example {example_hint}"
                    lines.append(line)
                    detail_lines.extend(
                        _build_schema_detail_lines(
                            spec,
                            media_dict.get("schema"),
                            prefix=f"{content_type} ",
                            example_payload=_extract_example_payload(spec, media_dict),
                            include_required=True,
                        )
                    )
    else:
        for param in params:
            if _string_or_none(param.get("in")) != "body":
                continue
            content_types.append("application/json")
            schema_hint = _summarize_schema(spec, param.get("schema"))
            description = _string_or_none(param.get("description"))
            line = f"- application/json ({'required' if param.get('required') else 'optional'}): {schema_hint}"
            if description:
                line += f"; {description}"
            lines.append(line)
            detail_lines.extend(
                _build_schema_detail_lines(
                    spec,
                    param.get("schema"),
                    prefix="application/json ",
                    example_payload=None,
                    include_required=True,
                )
            )

    return lines, detail_lines, _dedupe_preserve_order(content_types), has_examples


def _render_responses(
    spec: dict[str, Any], operation: dict[str, Any]
) -> tuple[list[str], list[str], list[str], list[str], bool]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return [], [], [], [], False

    lines: list[str] = []
    detail_lines: list[str] = []
    response_codes: list[str] = []
    content_types: list[str] = []
    has_examples = False
    for code, raw in responses.items():
        if not isinstance(code, str):
            continue
        resolved = _resolve_response_value(spec, raw)
        if not isinstance(resolved, dict):
            continue
        response_codes.append(code)
        description = _string_or_none(resolved.get("description")) or "No description"
        line = f"- {code}: {description}"
        content = resolved.get("content")
        media_bits: list[str] = []
        if isinstance(content, dict):
            for content_type, media in content.items():
                if not isinstance(content_type, str):
                    continue
                content_types.append(content_type)
                media_dict = media if isinstance(media, dict) else {}
                schema_hint = _summarize_schema(spec, media_dict.get("schema"))
                example_hint = _extract_example_hint(spec, media_dict)
                if example_hint:
                    has_examples = True
                bit = f"{content_type} {schema_hint}"
                if example_hint:
                    bit += f" example {example_hint}"
                media_bits.append(bit)
                detail_lines.extend(
                    _build_schema_detail_lines(
                        spec,
                        media_dict.get("schema"),
                        prefix=f"{code} {content_type} ",
                        example_payload=_extract_example_payload(spec, media_dict),
                        include_required=False,
                    )
                )
        elif "schema" in resolved:
            media_bits.append(_summarize_schema(spec, resolved.get("schema")))
            detail_lines.extend(
                _build_schema_detail_lines(
                    spec,
                    resolved.get("schema"),
                    prefix=f"{code} ",
                    example_payload=None,
                    include_required=False,
                )
            )
        if media_bits:
            line += f" [{'; '.join(media_bits[:_MAX_TEXT_ITEMS])}]"
        lines.append(line)
    return lines, detail_lines, response_codes, _dedupe_preserve_order(content_types), has_examples


def _resolve_security_names(spec: dict[str, Any], raw_security: Any) -> list[str]:
    if raw_security is None:
        return []

    out: list[str] = []
    components = spec.get("components") if isinstance(spec.get("components"), dict) else {}
    security_schemes = components.get("securitySchemes") if isinstance(components, dict) else {}
    swagger_security = spec.get("securityDefinitions") if isinstance(spec.get("securityDefinitions"), dict) else {}
    if not isinstance(raw_security, list):
        return out

    for requirement in raw_security:
        if not isinstance(requirement, dict):
            continue
        for scheme_name in requirement.keys():
            resolved_name = scheme_name
            scheme = None
            if isinstance(security_schemes, dict):
                scheme = security_schemes.get(scheme_name)
            if scheme is None and isinstance(swagger_security, dict):
                scheme = swagger_security.get(scheme_name)
            if isinstance(scheme, dict):
                scheme_type = _string_or_none(scheme.get("type"))
                if scheme_type:
                    resolved_name = f"{scheme_name} ({scheme_type})"
            out.append(resolved_name)
    return _dedupe_preserve_order(out)


def _render_extensions(operation: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in sorted(operation.keys()):
        if not isinstance(key, str) or not key.startswith("x-"):
            continue
        rendered = _render_extension_value(operation.get(key))
        if rendered:
            out.append(f"- {key}: {rendered}")
        if len(out) >= _MAX_EXTENSION_ITEMS:
            break
    return out


def _render_extension_value(value: Any) -> str | None:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, list):
        scalars = [str(item) for item in value if isinstance(item, (str, int, float, bool))]
        if scalars:
            return ", ".join(scalars[:_MAX_TEXT_ITEMS])
    return None


def _build_example_call(
    path: str,
    method: str,
    auth_schemes: list[str],
    params: list[dict[str, Any]],
) -> str | None:
    header_bits: list[str] = []
    query_bits: list[str] = []
    for param in params:
        name = _string_or_none(param.get("name"))
        location = _string_or_none(param.get("in"))
        if not name or not location:
            continue
        if location == "header":
            header_bits.append(f"-H '{name}: <value>'")
        elif location == "query":
            query_bits.append(f"{name}=<{name}>")
    if auth_schemes:
        header_bits.insert(0, "-H 'Authorization: Bearer <token>'")
    query_suffix = f"?{'&'.join(query_bits)}" if query_bits else ""
    joined_headers = (" " + " ".join(header_bits)) if header_bits else ""
    return f"curl -X {method.upper()}{joined_headers} '{{BASE_URL}}{path}{query_suffix}'"


def _extract_example_hint(spec: dict[str, Any], media: dict[str, Any]) -> str | None:
    example = _extract_example_payload(spec, media)
    if example is not None:
        return _compact_value(example)
    return None


def _extract_example_payload(spec: dict[str, Any], media: dict[str, Any]) -> Any | None:
    example = media.get("example")
    if example is not None:
        resolved = _resolve_local_value(spec, example)
        if isinstance(resolved, dict) and "value" in resolved:
            return resolved["value"]
        return resolved

    examples = media.get("examples")
    if isinstance(examples, dict):
        for item in examples.values():
            resolved = _resolve_local_value(spec, item)
            if isinstance(resolved, dict) and "value" in resolved:
                return resolved["value"]
            if resolved is not None:
                return resolved
    return None


def _compact_value(value: Any) -> str:
    if isinstance(value, str):
        return _sanitize_rendered_fragment(value)[:120]
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        preview = ", ".join(_compact_value(item) for item in value[:3])
        return f"[{preview}]"
    if isinstance(value, dict):
        keys = ", ".join(_sanitize_rendered_fragment(str(key)) for key in list(value.keys())[:5])
        return f"{{{keys}}}"
    return "example"


def _sanitize_rendered_fragment(text: str) -> str:
    sanitized = text
    for marker in (
        OPENAPI_OPERATION_SEPARATOR,
        OPENAPI_OPERATION_START_MARKER,
        OPENAPI_OPERATION_END_MARKER,
        OPENAPI_CHUNK_META_PREFIX,
        OPENAPI_REQUEST_DETAIL_MARKER,
        OPENAPI_RESPONSE_DETAIL_MARKER,
    ):
        sanitized = sanitized.replace(marker, " ")
    return sanitized


def _extend_visited_with_ref(value: Any, visited: set[str] | None) -> set[str] | None:
    if not isinstance(value, dict):
        return visited
    ref = value.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return visited
    base = set(visited or set())
    base.add(ref)
    return base


def _resolve_local_value(
    spec: dict[str, Any],
    value: Any,
    *,
    visited: set[str] | None = None,
    depth: int = 0,
) -> Any:
    if depth > _MAX_SCHEMA_DEPTH:
        return {"description": "Reference depth limit reached"}
    if not isinstance(value, dict) or "$ref" not in value:
        return value

    ref = value.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return {"description": f"Unresolved reference: {ref}"}
    visited = visited or set()
    if ref in visited:
        return {"description": f"Reference cycle detected: {ref}"}
    target = _resolve_json_pointer(spec, ref)
    if target is None:
        return {"description": f"Unresolved reference: {ref}"}
    return _resolve_local_value(spec, target, visited=visited | {ref}, depth=depth + 1)


def _resolve_response_value(
    spec: dict[str, Any], value: Any, *, visited: set[str] | None = None, depth: int = 0
) -> Any:
    active_visited = _extend_visited_with_ref(value, visited)
    resolved = _resolve_local_value(spec, value, visited=visited, depth=depth)
    if not isinstance(resolved, dict) or depth > _MAX_SCHEMA_DEPTH:
        return resolved
    all_of = resolved.get("allOf")
    if not isinstance(all_of, list):
        return resolved

    merged: dict[str, Any] = {}
    for part in all_of:
        part_resolved = _resolve_response_value(spec, part, visited=active_visited, depth=depth + 1)
        if not isinstance(part_resolved, dict):
            continue
        for key, part_value in part_resolved.items():
            if key == "content" and isinstance(part_value, dict):
                existing = merged.get("content")
                if isinstance(existing, dict):
                    merged["content"] = {**existing, **part_value}
                else:
                    merged["content"] = dict(part_value)
            elif key == "description":
                existing_desc = _string_or_none(merged.get("description"))
                new_desc = _string_or_none(part_value)
                if new_desc and not existing_desc:
                    merged["description"] = new_desc
            elif key not in merged:
                merged[key] = part_value
    for key, value_item in resolved.items():
        if key != "allOf" and key not in merged:
            merged[key] = value_item
    return merged


def _resolve_json_pointer(spec: dict[str, Any], ref: str) -> Any | None:
    path = ref[2:]
    if not path:
        return spec
    current: Any = spec
    for part in path.split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _summarize_schema(spec: dict[str, Any], schema: Any, *, depth: int = 0) -> str:
    resolved = _normalize_schema(spec, schema, depth=depth)
    if not isinstance(resolved, dict):
        return "object"
    if depth > _MAX_SCHEMA_DEPTH:
        return "object"

    if "oneOf" in resolved and isinstance(resolved["oneOf"], list):
        return "oneOf(" + ", ".join(_summarize_schema(spec, item, depth=depth + 1) for item in resolved["oneOf"][:3]) + ")"
    if "anyOf" in resolved and isinstance(resolved["anyOf"], list):
        return "anyOf(" + ", ".join(_summarize_schema(spec, item, depth=depth + 1) for item in resolved["anyOf"][:3]) + ")"
    if "allOf" in resolved and isinstance(resolved["allOf"], list):
        return "allOf(" + ", ".join(_summarize_schema(spec, item, depth=depth + 1) for item in resolved["allOf"][:3]) + ")"

    schema_type = _string_or_none(resolved.get("type"))
    if schema_type == "array":
        return f"array[{_summarize_schema(spec, resolved.get('items'), depth=depth + 1)}]"
    if schema_type == "object" or "properties" in resolved:
        props = resolved.get("properties")
        if isinstance(props, dict) and props:
            names = list(props.keys())[:_MAX_SCHEMA_FIELDS]
            suffix = ", ..." if len(props) > _MAX_SCHEMA_FIELDS else ""
            return f"object{{{', '.join(names)}{suffix}}}"
        return "object"
    if schema_type:
        return schema_type
    if "$ref" in resolved:
        return _string_or_none(resolved.get("$ref")) or "object"
    return "object"


def _normalize_schema(
    spec: dict[str, Any],
    schema: Any,
    *,
    visited: set[str] | None = None,
    depth: int = 0,
) -> Any:
    active_visited = _extend_visited_with_ref(schema, visited)
    resolved = _resolve_local_value(spec, schema, visited=visited, depth=depth)
    if not isinstance(resolved, dict):
        return resolved
    if depth > _MAX_SCHEMA_DEPTH:
        return resolved

    all_of = resolved.get("allOf")
    if not isinstance(all_of, list):
        return resolved

    merged: dict[str, Any] = {}
    merged_properties: dict[str, Any] = {}
    merged_required: list[str] = []
    saw_object_like = False
    for part in all_of:
        part_resolved = _normalize_schema(spec, part, visited=active_visited, depth=depth + 1)
        if not isinstance(part_resolved, dict):
            continue
        properties = part_resolved.get("properties")
        if isinstance(properties, dict):
            merged_properties.update(properties)
            saw_object_like = True
        if isinstance(part_resolved.get("required"), list):
            for item in part_resolved["required"]:
                if isinstance(item, str) and item not in merged_required:
                    merged_required.append(item)
        for key, value in part_resolved.items():
            if key in {"properties", "required", "allOf"}:
                continue
            if key not in merged:
                merged[key] = value

    if saw_object_like:
        merged["type"] = "object"
        merged["properties"] = merged_properties
    if merged_required:
        merged["required"] = merged_required
    if not merged:
        return resolved
    return merged


def _build_schema_detail_lines(
    spec: dict[str, Any],
    schema: Any,
    *,
    prefix: str,
    example_payload: Any | None,
    include_required: bool,
) -> list[str]:
    normalized = _normalize_schema(spec, schema)
    if not isinstance(normalized, dict):
        return []

    detail_lines: list[str] = []
    props = normalized.get("properties")
    required = [
        item for item in normalized.get("required", [])
        if isinstance(item, str)
    ] if include_required and isinstance(normalized.get("required"), list) else []

    if required:
        detail_lines.append(f"- {prefix}required fields: {', '.join(required)}")

    if isinstance(props, dict) and props:
        if len(props) >= _RICH_SCHEMA_PROPERTY_THRESHOLD or required or example_payload is not None:
            detail_lines.append(f"- {prefix}top-level fields:")
            for name, prop_schema in list(props.items())[:_MAX_SCHEMA_FIELDS]:
                detail_lines.append(
                    f"- {prefix}{name}: {_summarize_schema(spec, prop_schema)}"
                )
                detail_lines.extend(
                    _build_nested_schema_lines(
                        spec,
                        prop_schema,
                        prefix=prefix,
                        parent_name=name,
                    )
                )
            if len(props) > _MAX_SCHEMA_FIELDS:
                detail_lines.append(f"- {prefix}additional fields omitted")

    if example_payload is not None:
        detail_lines.append(f"- {prefix}example: {_compact_value(example_payload)}")

    return detail_lines


def _build_nested_schema_lines(
    spec: dict[str, Any],
    schema: Any,
    *,
    prefix: str,
    parent_name: str,
) -> list[str]:
    normalized = _normalize_schema(spec, schema)
    if not isinstance(normalized, dict):
        return []

    nested_props = normalized.get("properties")
    if not isinstance(nested_props, dict) or not nested_props:
        return []

    lines = [f"- {prefix}{parent_name} nested fields:"]
    for child_name, child_schema in list(nested_props.items())[:_NESTED_SCHEMA_DETAIL_LIMIT]:
        lines.append(
            f"- {prefix}field path: {parent_name}.{child_name}"
        )
        lines.append(
            f"- {prefix}{parent_name}.{child_name}: {_summarize_schema(spec, child_schema)}"
        )
    if len(nested_props) > _NESTED_SCHEMA_DETAIL_LIMIT:
        lines.append(f"- {prefix}{parent_name} additional nested fields omitted")
    return lines


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _string_or_none(item)
        if text:
            out.append(text)
    return _dedupe_preserve_order(out)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _parse_rendered_openapi_block(
    text: str, *, source_format: str, spec_version: str
) -> OpenAPIChunk | None:
    endpoint = None
    operation_id = None
    tags: list[str] = []
    deprecated = False
    auth_schemes: list[str] = []
    response_codes: list[str] = []
    content_types: list[str] = []
    has_examples = False
    current_section: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Endpoint: "):
            endpoint = stripped.removeprefix("Endpoint: ").strip()
        elif stripped.startswith("Operation ID: "):
            operation_id = stripped.removeprefix("Operation ID: ").strip() or None
        elif stripped.startswith("Tags: "):
            tags = [item.strip() for item in stripped.removeprefix("Tags: ").split(",") if item.strip()]
        elif stripped == "Status: Deprecated":
            deprecated = True
        elif stripped.startswith("Authentication: "):
            auth_schemes = [item.strip() for item in stripped.removeprefix("Authentication: ").split(",") if item.strip()]
        elif stripped == "Request Body:":
            current_section = "request"
        elif stripped == "Responses:":
            current_section = "response"
        elif stripped in {"Request Schema Detail:", "Response Schema Detail:", "Example Call:", "Extensions:"}:
            current_section = None
        elif stripped.startswith("- ") and ": " in stripped:
            head = stripped[2:].split(":", 1)[0].strip()
            if head.isdigit() or head == "default":
                response_codes.append(head)
            if current_section in {"request", "response"}:
                content_types.extend(_extract_content_types_from_line(stripped))
            if "example " in stripped:
                has_examples = True
        elif stripped.startswith("curl -X "):
            has_examples = True

    if not endpoint or " " not in endpoint:
        return None
    method, path = endpoint.split(" ", 1)
    return OpenAPIChunk(
        text=text,
        path=path.strip(),
        method=method.strip().lower(),
        operation_id=operation_id,
        tags=tags,
        deprecated=deprecated,
        content_types=_dedupe_preserve_order(content_types),
        response_codes=_dedupe_preserve_order(response_codes),
        auth_schemes=_dedupe_preserve_order(auth_schemes),
        has_examples=has_examples,
        source_format=source_format,
        spec_version=spec_version,
    )


def _extract_content_types_from_line(line: str) -> list[str]:
    found: list[str] = []
    for token in line.replace("[", " ").replace("]", " ").replace(";", " ").split():
        clean = token.strip(",)")
        if clean.startswith("application/") or clean.startswith("text/"):
            found.append(clean)
    return found
