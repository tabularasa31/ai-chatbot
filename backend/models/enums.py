from __future__ import annotations

import enum


class DocumentType(str, enum.Enum):
    pdf = "pdf"
    markdown = "markdown"
    swagger = "swagger"
    url = "url"
    docx = "docx"
    plaintext = "plaintext"


class DocumentStatus(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    embedding = "embedding"
    error = "error"


class SourceStatus(str, enum.Enum):
    queued = "queued"
    indexing = "indexing"
    ready = "ready"
    stale = "stale"
    error = "error"
    paused = "paused"


class SourceSchedule(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"
    manual = "manual"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class MessageFeedback(str, enum.Enum):
    none = "none"
    up = "up"
    down = "down"


class EscalationTrigger(str, enum.Enum):
    low_similarity = "low_similarity"
    no_documents = "no_documents"
    user_request = "user_request"
    answer_rejected = "answer_rejected"


class EscalationPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class EscalationStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"


class PiiEventDirection(str, enum.Enum):
    message_storage = "message_storage"
    escalation_ticket = "escalation_ticket"
    notification_email = "notification_email"
    original_view = "original_view"
    original_delete = "original_delete"


class EscalationPhase(str, enum.Enum):
    """OpenAI escalation UX phases (fact_json), not stored on DB."""

    handoff_email_known = "handoff_email_known"
    handoff_ask_email = "handoff_ask_email"
    email_parse_failed = "email_parse_failed"
    followup_awaiting_yes_no = "followup_awaiting_yes_no"
    chat_already_closed = "chat_already_closed"
