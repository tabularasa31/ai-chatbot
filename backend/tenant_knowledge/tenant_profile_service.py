from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy.orm import Session

from backend.models import TenantProfile as TenantProfileModel
from backend.tenant_knowledge.schemas import AliasEntry, GlossaryEntry


def _norm_term(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def load_tenant_profile(db: Session, tenant_id: uuid.UUID) -> TenantProfileModel | None:
    """Return tenant profile row or None if missing."""
    return db.get(TenantProfileModel, tenant_id)


def _merge_modules(existing: list[str], incoming: Iterable[str]) -> list[str]:
    seen: set[str] = {m.casefold() for m in existing if isinstance(m, str)}
    out = list(existing)
    for m in incoming:
        if not isinstance(m, str):
            continue
        if m.casefold() in seen:
            continue
        seen.add(m.casefold())
        out.append(m.strip())
    return out


def _merge_glossary(
    existing: list[dict] | None,
    incoming: list[GlossaryEntry],
) -> list[dict]:
    existing_list = existing or []
    out_by_norm: dict[str, dict] = {}
    for item in existing_list:
        if not isinstance(item, dict):
            continue
        term = item.get("term")
        if not isinstance(term, str) or not term.strip():
            continue
        norm = _norm_term(term)
        out_by_norm[norm] = item

    for entry in incoming:
        norm = _norm_term(entry.term)
        payload = {
            "term": entry.term.strip(),
            "definition": entry.definition.strip()
            if isinstance(entry.definition, str) and entry.definition.strip()
            else None,
            "confidence": float(entry.confidence),
            "source": str(entry.source),
        }
        existing_item = out_by_norm.get(norm)
        if existing_item is None:
            out_by_norm[norm] = payload
            continue

        old_conf = existing_item.get("confidence")
        try:
            old_conf_f = float(old_conf) if old_conf is not None else 0.0
        except Exception:
            old_conf_f = 0.0

        # Only update definition/source when confidence improves.
        if payload["confidence"] > old_conf_f:
            out_by_norm[norm] = payload

    # stable output order (by normalized term sort)
    return [out_by_norm[k] for k in sorted(out_by_norm.keys())]


def _merge_aliases(
    existing: list[dict] | None,
    incoming: list[AliasEntry],
) -> list[dict]:
    existing_list = existing or []
    out_by_user_phrase: dict[str, dict] = {}
    for item in existing_list:
        if not isinstance(item, dict):
            continue
        up = item.get("user_phrase")
        if not isinstance(up, str) or not up.strip():
            continue
        out_by_user_phrase[up.casefold()] = item

    for entry in incoming:
        key = entry.user_phrase.strip().casefold()
        payload = {
            "user_phrase": entry.user_phrase.strip(),
            "canonical_term": entry.canonical_term.strip(),
            "confidence": float(entry.confidence),
        }
        existing_item = out_by_user_phrase.get(key)
        if existing_item is None:
            out_by_user_phrase[key] = payload
            continue

        old_conf = existing_item.get("confidence")
        try:
            old_conf_f = float(old_conf) if old_conf is not None else 0.0
        except Exception:
            old_conf_f = 0.0
        if payload["confidence"] > old_conf_f:
            out_by_user_phrase[key] = payload

    return list(out_by_user_phrase.values())


def merge_into_profile(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    product_name: str | None,
    product_name_confidence: float,
    modules: list[str],
    glossary_entries: list[GlossaryEntry],
    support_email: str | None,
    support_email_confidence: float,
    support_urls: list[str],
    escalation_policy: str | None,
    aliases: list[AliasEntry],
    updated_at,
) -> TenantProfileModel:
    """
    Merge extracted knowledge into `tenant_profiles` row.

    Confidence gates follow the Phase 1 spec:
    - product_name overwritten only if new_conf >= 0.85 or old is empty
    - support_email overwritten only if new_conf >= 0.85
    - glossary updated by term; definition updated only if confidence improves
    """
    row = load_tenant_profile(db, tenant_id)
    if row is None:
        row = TenantProfileModel(
            tenant_id=tenant_id,
            product_name=None,
            modules=[],
            glossary=[],
            aliases=[],
            support_email=None,
            support_urls=[],
            escalation_policy=None,
        )
        db.add(row)

    old_product = row.product_name
    if (
        not old_product
        or not isinstance(old_product, str)
        or not old_product.strip()
        or product_name_confidence >= 0.85
    ):
        if isinstance(product_name, str) and product_name.strip():
            row.product_name = product_name.strip()

    row.modules = _merge_modules(row.modules or [], modules)
    row.glossary = _merge_glossary(row.glossary if isinstance(row.glossary, list) else None, glossary_entries)
    row.support_urls = _merge_modules(row.support_urls or [], support_urls)

    if support_email and support_email_confidence >= 0.85:
        row.support_email = support_email.strip()

    if escalation_policy is not None and isinstance(escalation_policy, str):
        if not row.escalation_policy:
            row.escalation_policy = escalation_policy.strip()

    row.aliases = _merge_aliases(row.aliases if isinstance(row.aliases, list) else None, aliases)
    row.updated_at = updated_at
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

