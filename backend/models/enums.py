from __future__ import annotations

import enum


class DocumentType(str, enum.Enum):
    pdf = "pdf"
    markdown = "markdown"
    swagger = "swagger"
    url = "url"
    docx = "docx"
    plaintext = "plaintext"
    html = "html"


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
    # A human operator answering the visitor directly, through any
    # ``OperatorChannel`` (dashboard console, inbound e-mail reply, later
    # Telegram / Slack). Deliberately distinct from ``assistant``: the bot's
    # quality metrics, loop detection and FAQ mining must never count a human
    # reply as a bot reply. Value fits the existing VARCHAR(9) column, so no
    # column alteration is needed.
    operator = "operator"


class OperatorState(str, enum.Enum):
    """Who is answering the visitor in a chat.

    Only two values on purpose. "Waiting for an operator" is *derived* — an
    open ``EscalationTicket`` whose chat has no ``assigned_operator_id`` — and
    is never stored: a second persisted state would be a second FSM alongside
    the escalation one, and the two would drift.
    """

    # The bot answers normally. Default for every chat.
    bot = "bot"
    # A human operator has taken over; the bot is fully muted for this chat.
    live = "live"


class OperatorSessionEndReason(str, enum.Enum):
    """Why an operator-served stretch of a conversation ended.

    Recorded on ``operator_sessions.ended_reason`` and carried on the
    ``operator_session_ended`` analytics event. The distinctions are the ones
    a support team reads differently, not merely the code paths: a stretch an
    operator closed deliberately, one that timed out with nobody left in the
    room, and one handed back because the visitor came back to a silent
    operator all mean different things about how the handoff went.
    """

    # An operator handed the chat back explicitly (console button / API).
    released = "released"
    # The operator went silent past ``OPERATOR_RELEASE_IDLE_SECONDS`` and
    # nobody wrote again — the sweeper's backstop release closed the stretch.
    # The ordinary end of a conversation that simply finished.
    idle_timeout = "idle_timeout"
    # The visitor wrote again while the operator was idle past the same
    # window, so the bot took the turn (lazy release in ``OperatorHandler``).
    # Same rule as ``idle_timeout``, different trigger — and the difference
    # matters: here the conversation was still going when the human dropped
    # out of it.
    visitor_returned = "visitor_returned"
    # The row was found open while its chat was already back in ``bot``: a
    # release whose row-close did not land (a crash between the two writes) or
    # a chat that was live before this table existed. Closed by the sweeper's
    # reconciliation pass at the chat's own release time, not sweep time.
    reconciled = "reconciled"


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
    # Terminal, set by the sweeper when the conversation behind the ticket went
    # idle past the shared "conversation is over" window. Deliberately distinct
    # from ``resolved``: nothing here claims support handled the request, and
    # collapsing the two would destroy the only signal the inbox has about what
    # still needs a human. Column is VARCHAR(11) — this value fits exactly.
    auto_closed = "auto_closed"


class PiiEventDirection(str, enum.Enum):
    """Outbound boundary a redaction was applied at.

    Storage holds the user's original wording; redaction happens where text
    leaves the platform, so every direction names an egress: an OpenAI
    request, the escalation ticket forwarded to support, or a notification
    e-mail.
    """

    llm_request = "llm_request"
    escalation_ticket = "escalation_ticket"
    notification_email = "notification_email"


class EscalationPhase(str, enum.Enum):
    """OpenAI escalation UX phases (fact_json), not stored on DB."""

    pre_confirm = "pre_confirm"
    handoff_email_known = "handoff_email_known"
    handoff_ask_email = "handoff_ask_email"
    email_parse_failed = "email_parse_failed"
    followup_awaiting_yes_no = "followup_awaiting_yes_no"
    chat_already_closed = "chat_already_closed"
