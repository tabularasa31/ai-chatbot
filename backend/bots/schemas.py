from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DisclosureLevelLiteral = Literal["detailed", "standard", "corporate"]


class BotResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    public_id: str
    is_active: bool
    link_safety_enabled: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    agent_instructions: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _coerce_allowed_domains(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]


class BotCreate(BaseModel):
    name: str
    agent_instructions: str | None = None
    website_url: str | None = None
    link_safety_enabled: bool | None = None
    allowed_domains: list[str] | None = None


class BotUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    agent_instructions: str | None = None
    link_safety_enabled: bool | None = None
    allowed_domains: list[str] | None = None


class BotList(BaseModel):
    items: list[BotResponse]


class DisclosureConfigResponse(BaseModel):
    level: DisclosureLevelLiteral


class DisclosureConfigUpdate(BaseModel):
    level: DisclosureLevelLiteral
