"""Pydantic schemas for admin metrics API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class AdminMetricsSummary(BaseModel):
    """Platform-wide metrics summary."""

    total_users: int
    total_clients: int
    active_clients: int
    total_documents: int
    total_chat_sessions: int
    total_messages_user: int
    total_messages_assistant: int
    total_tokens_chat: int


class AdminClientMetricsItem(BaseModel):
    """Per-client metrics row."""

    client_id: UUID
    name: str
    users_count: int
    documents_count: int
    embedded_documents_count: int
    chat_sessions_count: int
    messages_user_count: int
    messages_assistant_count: int
    tokens_used_chat: int
    has_openai_key: bool


class AdminClientMetricsList(BaseModel):
    """List of per-client metrics."""

    items: list[AdminClientMetricsItem]
