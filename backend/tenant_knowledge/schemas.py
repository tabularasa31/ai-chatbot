from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


KnowledgeSource = Literal["docs", "logs", "swagger"]


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    definition: str | None
    confidence: float
    source: KnowledgeSource


@dataclass(frozen=True)
class AliasEntry:
    user_phrase: str
    canonical_term: str
    confidence: float


@dataclass
class TenantProfile:
    client_id: object  # use app-level uuid/str without importing models here
    product_name: str | None
    modules: list[str]
    glossary: list[GlossaryEntry]
    support_email: str | None
    support_urls: list[str]
    escalation_policy: str | None
    aliases: list[AliasEntry]
    last_updated_at: datetime


@dataclass(frozen=True)
class FaqCandidate:
    question: str
    answer: str
    confidence: float
    source: KnowledgeSource
