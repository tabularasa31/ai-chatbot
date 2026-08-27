"""Request / response schemas for the operator API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# Upper bound on one operator message. Generous compared with the visitor's
# ``WIDGET_MESSAGE_MAX_CHARS``: a support reply routinely carries a
# configuration snippet or a quoted error, and the two limits protect
# different things — this one only guards against an unbounded write.
OPERATOR_MESSAGE_MAX_CHARS = 10_000


class OperatorChatStateResponse(BaseModel):
    """Handoff state of one chat, returned by every operator write route."""

    chat_id: uuid.UUID
    operator_state: str
    assigned_operator_id: uuid.UUID | None = None
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
