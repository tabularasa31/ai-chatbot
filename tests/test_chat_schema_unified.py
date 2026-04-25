"""Verify private /chat and widget /chat advertise the same ChatTurnResponse in OpenAPI."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _component_ref_for(spec: dict, path: str, method: str, media_type: str) -> str | None:
    operation = spec["paths"][path][method]
    response = operation["responses"]["200"]
    schema = response.get("content", {}).get(media_type, {}).get("schema")
    if schema is None:
        return None
    if "$ref" in schema:
        return schema["$ref"]
    if "items" in schema and "$ref" in schema["items"]:
        return schema["items"]["$ref"]
    return None


def test_private_and_widget_chat_share_chat_turn_response_schema(tenant: TestClient) -> None:
    """Private /chat (JSON) and widget /chat (SSE) must reference the same component.

    Each endpoint advertises the schema under the media type it actually serves —
    `application/json` for the private API, `text/event-stream` for the widget —
    so OpenAPI client generators see the right wire protocol on each side.
    """
    spec = tenant.get("/openapi.json").json()

    private_ref = _component_ref_for(spec, "/chat", "post", "application/json")
    widget_ref = _component_ref_for(spec, "/widget/chat", "post", "text/event-stream")

    assert private_ref is not None, "private /chat should advertise an application/json schema"
    assert widget_ref is not None, (
        "widget /chat should advertise a text/event-stream schema for the SSE done payload"
    )
    assert private_ref.endswith("/ChatTurnResponse"), (
        f"private /chat must reference ChatTurnResponse, got {private_ref}"
    )
    assert private_ref == widget_ref, (
        "private and widget chat endpoints must share the same response schema "
        f"(got private={private_ref}, widget={widget_ref})"
    )

    # Widget must NOT advertise itself as application/json (it streams SSE).
    widget_json_ref = _component_ref_for(spec, "/widget/chat", "post", "application/json")
    assert widget_json_ref is None, (
        "widget /chat must not advertise application/json — it streams SSE; "
        f"got {widget_json_ref}"
    )

    schema = spec["components"]["schemas"]["ChatTurnResponse"]
    properties = schema["properties"]
    assert set(properties.keys()) == {
        "text",
        "session_id",
        "chat_ended",
        "ticket_number",
        "source_documents",
        "tokens_used",
    }
    # `validation` was removed — guard against accidental reintroduction.
    assert "validation" not in properties
