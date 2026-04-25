"""Verify private /chat and widget /chat advertise the same ChatTurnResponse in OpenAPI."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _component_ref_for(spec: dict, path: str, method: str) -> str | None:
    operation = spec["paths"][path][method]
    response = operation["responses"]["200"]
    schema = response.get("content", {}).get("application/json", {}).get("schema")
    if schema is None:
        return None
    if "$ref" in schema:
        return schema["$ref"]
    if "items" in schema and "$ref" in schema["items"]:
        return schema["items"]["$ref"]
    return None


def test_private_and_widget_chat_share_chat_turn_response_schema(tenant: TestClient) -> None:
    spec = tenant.get("/openapi.json").json()

    private_ref = _component_ref_for(spec, "/chat", "post")
    widget_ref = _component_ref_for(spec, "/widget/chat", "post")

    assert private_ref is not None, "private /chat should advertise a JSON response schema"
    assert widget_ref is not None, "widget /chat should advertise the SSE done payload schema"
    assert private_ref.endswith("/ChatTurnResponse"), (
        f"private /chat must reference ChatTurnResponse, got {private_ref}"
    )
    assert private_ref == widget_ref, (
        "private and widget chat endpoints must share the same response schema "
        f"(got private={private_ref}, widget={widget_ref})"
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
