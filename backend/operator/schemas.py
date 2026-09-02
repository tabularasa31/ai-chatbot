"""Request / response schemas for the operator API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Upper bound on one operator message. Generous compared with the visitor's
# ``WIDGET_MESSAGE_MAX_CHARS``: a support reply routinely carries a
# configuration snippet or a quoted error, and the two limits protect
# different things — this one only guards against an unbounded write.
OPERATOR_MESSAGE_MAX_CHARS = 10_000

HandoffStateValue = Literal["waiting", "live", "bot"]


class OperatorChatStateResponse(BaseModel):
    """Handoff state of one chat, returned by every operator write route."""

    chat_id: uuid.UUID
    operator_state: str
    assigned_operator_id: uuid.UUID | None = None
    assigned_operator_email: str | None = None
    operator_joined_at: datetime | None = None
    operator_released_at: datetime | None = None


class OperatorMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=OPERATOR_MESSAGE_MAX_CHARS)


class OperatorMessageResponse(BaseModel):
    """The persisted operator message plus the resulting chat state."""

    message_id: uuid.UUID
    created_at: datetime
    chat: OperatorChatStateResponse
    # True when this message reopened a conversation the visitor had closed
    # ("no, that's all") before the operator got to it. The visitor's input is
    # unlocked again, so they can answer the human who just wrote to them.
    chat_reopened: bool = False


class OperatorResolveRequest(BaseModel):
    resolution_text: str | None = Field(default=None, max_length=8000)


class OperatorResolveResponse(BaseModel):
    chat: OperatorChatStateResponse
    resolved_ticket_numbers: list[str]


class InboxTicket(BaseModel):
    id: uuid.UUID
    ticket_number: str
    status: str
    priority: str
    trigger: str
    user_note: str | None = None
    resolution_text: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class InboxRowResponse(BaseModel):
    session_id: uuid.UUID
    chat_id: uuid.UUID
    handoff_state: HandoffStateValue
    ticket: InboxTicket | None = None
    assigned_operator_id: uuid.UUID | None = None
    assigned_operator_email: str | None = None
    waiting_since: datetime | None = None
    last_message_role: str | None = None
    last_message_preview: str | None = None
    last_activity: datetime
    message_count: int
    visitor_email: str | None = None
    visitor_name: str | None = None


class InboxListResponse(BaseModel):
    items: list[InboxRowResponse]
    waiting_count: int
    attention_count: int


class InboxSummaryResponse(BaseModel):
    waiting_count: int
    attention_count: int


class ThreadMessageResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    # The operator's signature on their own turns: their e-mail while the
    # account exists, the copy kept on the row once it is gone, and nothing
    # for an unattributed reply. Never sent to the visitor.
    author_label: str | None = None


class ThreadResponse(BaseModel):
    session_id: uuid.UUID
    chat: OperatorChatStateResponse
    handoff_state: HandoffStateValue
    chat_ended: bool
    ticket: InboxTicket | None = None
    visitor_email: str | None = None
    visitor_name: str | None = None
    messages: list[ThreadMessageResponse]
