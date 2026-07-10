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
    llm_unavailable = "llm_unavailable"
    # The LLM ended its reply with the OFFER_MARKER sentinel even though the
    # retrieval classifier (decide()) judged the turn answerable. Distinct
    # from low_similarity so support-team handoff emails and PostHog funnels
    # don't conflate "retrieval was poor" with "model judged itself short on
    # information despite a healthy KB hit".
    llm_self_offer = "llm_self_offer"
    # The relevance guard classified the message as a complaint about support
    # being unresponsive (waiting on a reply, being ignored). Routed to the
    # pre-confirm escalation offer instead of an off-topic reject. Value must
    # stay ≤ 15 chars: the escalation_tickets.trigger column was narrowed to
    # the longest legacy value's length (VARCHAR(15)) by 67aaa83e5689.
    user_complaint = "user_complaint"


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

    pre_confirm = "pre_confirm"
    handoff_email_known = "handoff_email_known"
    handoff_ask_email = "handoff_ask_email"
    email_parse_failed = "email_parse_failed"
    followup_awaiting_yes_no = "followup_awaiting_yes_no"
    chat_already_closed = "chat_already_closed"
