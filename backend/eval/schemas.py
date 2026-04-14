from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator


class EvalLoginRequest(BaseModel):
    username: str
    password: str


class EvalTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class EvalSessionCreateRequest(BaseModel):
    bot_id: str

    @field_validator("bot_id")
    @classmethod
    def bot_id_non_empty(cls, v: str) -> str:
        s = v.strip() if isinstance(v, str) else ""
        if not s:
            raise ValueError("bot_id is required")
        return s


class EvalSessionResponse(BaseModel):
    id: UUID
    tester_id: UUID
    bot_id: str
    started_at: datetime


EvalVerdict = Literal["pass", "fail"]
EvalErrorCategory = Literal[
    "hallucination",
    "incomplete",
    "wrong_generation",
    "off_topic",
    "no_answer",
    "other",
]


class EvalResultCreateRequest(BaseModel):
    question: str
    bot_answer: str
    verdict: EvalVerdict
    error_category: EvalErrorCategory | None = None
    comment: str | None = None

    @field_validator("question")
    @classmethod
    def question_required(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("must be a string")
        t = v.strip()
        if not t:
            raise ValueError("must not be empty")
        return t

    @field_validator("bot_answer")
    @classmethod
    def normalize_bot_answer(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return v.strip()

    @model_validator(mode="after")
    def verdict_rules(self) -> EvalResultCreateRequest:
        if self.verdict == "pass":
            if self.error_category is not None:
                raise ValueError("error_category must be null when verdict is pass")
            return self
        # fail
        if self.error_category == "other":
            c = (self.comment or "").strip()
            if not c:
                raise ValueError("comment is required when error_category is other")
            self.comment = c
        return self


class EvalResultCreateResponse(BaseModel):
    id: UUID
    session_id: UUID
    created_at: datetime


class EvalResultItemResponse(BaseModel):
    id: UUID
    session_id: UUID
    question: str
    bot_answer: str
    verdict: str
    error_category: str | None
    comment: str | None
    created_at: datetime


class EvalResultListResponse(BaseModel):
    items: list[EvalResultItemResponse]
