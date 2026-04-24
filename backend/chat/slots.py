"""Per-intent slot registry for the clarification policy.

Slots are defined per intent, not globally. Severity controls whether a
missing slot is grounds for a blocking clarification (CRITICAL), an inline
caveat-and-ask (HIGH), or a quiet omission (MEDIUM).

Extend this table as new intents are added to the classifier. The intent
class names here must match whatever the intent classifier (or retrieval
heuristic) produces.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SlotSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


@dataclass(frozen=True)
class SlotDef:
    intent: str
    name: str
    severity: SlotSeverity


SLOT_REGISTRY: list[SlotDef] = [
    SlotDef("billing_question", "account_id", SlotSeverity.CRITICAL),
    SlotDef("billing_question", "invoice_period", SlotSeverity.HIGH),
    SlotDef("api_error", "endpoint", SlotSeverity.CRITICAL),
    SlotDef("api_error", "error_code", SlotSeverity.HIGH),
    SlotDef("api_error", "request_id", SlotSeverity.MEDIUM),
    SlotDef("feature_question", "product_area", SlotSeverity.HIGH),
    SlotDef("outage_report", "affected_service", SlotSeverity.CRITICAL),
    SlotDef("outage_report", "first_seen_at", SlotSeverity.HIGH),
]


def critical_slots_for_intent(intent: str | None) -> list[str]:
    """Return names of CRITICAL slots for the given intent class."""
    if not intent:
        return []
    return [s.name for s in SLOT_REGISTRY if s.intent == intent and s.severity == SlotSeverity.CRITICAL]


def high_slots_for_intent(intent: str | None) -> list[str]:
    """Return names of HIGH-severity slots for the given intent class."""
    if not intent:
        return []
    return [s.name for s in SLOT_REGISTRY if s.intent == intent and s.severity == SlotSeverity.HIGH]
