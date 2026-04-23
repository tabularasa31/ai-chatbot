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
    agent_instructions: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class BotCreate(BaseModel):
    name: str
    agent_instructions: str | None = None
    website_url: str | None = None


class BotUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    agent_instructions: str | None = None


class BotList(BaseModel):
    items: list[BotResponse]


class DisclosureConfigResponse(BaseModel):
    level: DisclosureLevelLiteral


class DisclosureConfigUpdate(BaseModel):
    level: DisclosureLevelLiteral
