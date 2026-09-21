from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.chat.presets import PRESETS, effective_agent_instructions

DisclosureLevelLiteral = Literal["detailed", "standard", "corporate"]

_MAX_CUSTOM_INSTRUCTIONS_LENGTH = 3000


class BotResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    public_id: str
    is_active: bool
    link_safety_enabled: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    agent_instructions: str | None = Field(
        default=None,
        description="Deprecated. Use custom_instructions and preset instead.",
    )
    custom_instructions: str | None = None
    preset: str | None = None
    effective_instructions: str | None = None
    instructions_source: str = "none"
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _coerce_allowed_domains(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    @classmethod
    def from_bot(cls, bot: object) -> BotResponse:
        text, source = effective_agent_instructions(
            agent_instructions=getattr(bot, "agent_instructions", None),
            custom_instructions=getattr(bot, "custom_instructions", None),
            preset=getattr(bot, "preset", None),
        )
        return cls.model_validate(bot).model_copy(
            update={"effective_instructions": text, "instructions_source": source}
        )


def _validate_preset(value: str | None) -> str | None:
    if value is not None and value not in PRESETS:
        raise ValueError(f"preset must be one of: {', '.join(sorted(PRESETS))}")
    return value


class BotCreate(BaseModel):
    name: str
    agent_instructions: str | None = None
    custom_instructions: str | None = Field(default=None, max_length=_MAX_CUSTOM_INSTRUCTIONS_LENGTH)
    preset: str | None = None
    website_url: str | None = None
    link_safety_enabled: bool | None = None
    allowed_domains: list[str] | None = None

    @field_validator("preset")
    @classmethod
    def _validate_preset_field(cls, value: str | None) -> str | None:
        return _validate_preset(value)


class BotUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    agent_instructions: str | None = None
    custom_instructions: str | None = Field(default=None, max_length=_MAX_CUSTOM_INSTRUCTIONS_LENGTH)
    preset: str | None = None
    link_safety_enabled: bool | None = None
    allowed_domains: list[str] | None = None

    @field_validator("preset")
    @classmethod
    def _validate_preset_field(cls, value: str | None) -> str | None:
        return _validate_preset(value)


class BotList(BaseModel):
    items: list[BotResponse]


class DisclosureConfigResponse(BaseModel):
    level: DisclosureLevelLiteral


class DisclosureConfigUpdate(BaseModel):
    level: DisclosureLevelLiteral
