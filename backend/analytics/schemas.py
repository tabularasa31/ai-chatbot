"""DTOs for the tenant analytics summary endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnalyticsPeriod = Literal["7d", "30d", "90d"]


class AnalyticsSummaryResponse(BaseModel):
    """The five on-the-fly numbers for one rolling window.

    ``from`` is a Python keyword, so the field is named ``from_`` and
    serialized under its alias; ``populate_by_name`` lets callers in this
    codebase construct it with the ``from_=`` keyword.
    """

    model_config = ConfigDict(populate_by_name=True)

    period: AnalyticsPeriod
    from_: datetime = Field(alias="from")
    to: datetime
    messages: int
    conversations: int
    deflection_rate: float | None = None
    answered_rate: float | None = None
    filtered: int
