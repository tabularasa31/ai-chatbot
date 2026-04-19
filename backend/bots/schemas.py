from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel

DisclosureLevelLiteral = Literal["detailed", "standard", "corporate"]


class BotResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    public_id: str
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class BotCreate(BaseModel):
    name: str


class BotUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class BotList(BaseModel):
    items: list[BotResponse]


class DisclosureConfigResponse(BaseModel):
    level: DisclosureLevelLiteral


class DisclosureConfigUpdate(BaseModel):
    level: DisclosureLevelLiteral
