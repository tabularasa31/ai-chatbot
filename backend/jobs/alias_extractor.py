"""Phase 4 — Alias extraction from clustered chat messages.

Extracts user_phrase → canonical_term aliases from semantically similar
but differently-worded user questions.

Key design decisions:
- Pre-filter: cluster_size >= 5 AND lexical_diversity > 0.6 before LLM call
- Dynamic confidence: new=0.7, grows by 0.1 per repeat appearance, max=0.9
- Semaphore: max 3 concurrent LLM calls
- Returns count of aliases created/updated
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from itertools import pairwise

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client

logger = logging.getLogger(__name__)

ALIAS_BASE_CONFIDENCE = 0.7
ALIAS_CONFIDENCE_INCREMENT = 0.1
ALIAS_CONFIDENCE_MAX = 0.9

_LLM_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _LLM_SEMAPHORE
    if _LLM_SEMAPHORE is None:
        _LLM_SEMAPHORE = asyncio.Semaphore(3)
    return _LLM_SEMAPHORE


@dataclass
class AliasEntry:
    user_phrase: str
    canonical_term: str
    confidence: float = ALIAS_BASE_CONFIDENCE


# ── Pre-filter ────────────────────────────────────────────────────────────────

def should_extract_aliases(cluster_questions: list[str]) -> bool:
    """Return True only if cluster warrants LLM alias extraction.

    Conditions (both must hold):
    - cluster_size >= ALIAS_MIN_CLUSTER_SIZE (default 5)
    - lexical_diversity > ALIAS_MIN_DIVERSITY (default 0.6)

    Rationale: if all questions are nearly identical (low diversity), aliases
    are obvious and LLM adds no value.  LLM is useful when questions vary in
    phrasing but are semantically close.
    """
    if len(cluster_questions) < settings.alias_min_cluster_size:
        return False

    all_bigrams: list[tuple[str, str]] = []
    for q in cluster_questions:
        tokens = q.lower().split()
        all_bigrams.extend(pairwise(tokens))

    if not all_bigrams:
        return False

    diversity = len(set(all_bigrams)) / len(all_bigrams)
    return diversity >= settings.alias_min_diversity


# ── LLM extraction ────────────────────────────────────────────────────────────

ALIAS_SYSTEM_PROMPT = (
    "Extract alias pairs from these similar user questions. "
    "Find phrases that mean the same thing but are worded differently. "
    'Return ONLY JSON: {"aliases": [{"user_phrase": "...", "canonical_term": "..."}]} '
    "Return empty aliases array if none found. No explanation."
)


async def _call_alias_llm(
    cluster_questions: list[str],
    api_key: str,
) -> list[AliasEntry]:
    """Call LLM to extract aliases. Throttled by semaphore (max 3 parallel)."""
    oai = get_openai_client(api_key)
    user_content = "Questions:\n" + "\n".join(cluster_questions)

    async with _get_semaphore():
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: oai.chat.completions.create(
                model=settings.extraction_model,
                messages=[
                    {"role": "system", "content": ALIAS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=512,
            ),
        )

    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
        entries = []
        for item in data.get("aliases", []):
            phrase = (item.get("user_phrase") or "").strip()
            canonical = (item.get("canonical_term") or "").strip()
            if phrase and canonical:
                entries.append(AliasEntry(user_phrase=phrase, canonical_term=canonical))
        return entries
    except Exception:
        logger.warning("Failed to parse alias LLM response: %r", raw[:200])
        return []


# ── Confidence management ─────────────────────────────────────────────────────

def _get_client_aliases(db: Session, tenant_id: uuid.UUID) -> dict[str, float]:
    """Return {lower(user_phrase): confidence} for existing tenant aliases."""
    from backend.models import TenantProfile

    profile = (
        db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant_id).first()
    )
    if profile is None or not profile.aliases:
        return {}

    result: dict[str, float] = {}
    for entry in profile.aliases:
        if isinstance(entry, dict):
            phrase = (entry.get("user_phrase") or "").strip().lower()
            conf = float(entry.get("confidence", ALIAS_BASE_CONFIDENCE))
            if phrase:
                result[phrase] = conf
    return result


def _merge_aliases_into_profile(
    db: Session,
    tenant_id: uuid.UUID,
    new_aliases: list[AliasEntry],
) -> int:
    """Upsert aliases into TenantProfile.aliases. Returns count of changes."""
    from backend.models import TenantProfile

    if not new_aliases:
        return 0

    profile = (
        db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant_id).first()
    )
    if profile is None:
        logger.debug("No TenantProfile for tenant %s — skipping alias merge", tenant_id)
        return 0

    existing_list: list[dict] = list(profile.aliases or [])
    existing_map: dict[str, int] = {}  # lower(phrase) → index in list
    for i, entry in enumerate(existing_list):
        if isinstance(entry, dict):
            phrase = (entry.get("user_phrase") or "").strip().lower()
            if phrase:
                existing_map[phrase] = i

    changed = 0
    for alias in new_aliases:
        key = alias.user_phrase.lower()
        if key in existing_map:
            idx = existing_map[key]
            old_conf = float(existing_list[idx].get("confidence", ALIAS_BASE_CONFIDENCE))
            new_conf = min(old_conf + ALIAS_CONFIDENCE_INCREMENT, ALIAS_CONFIDENCE_MAX)
            existing_list[idx] = {
                **existing_list[idx],
                "confidence": new_conf,
            }
        else:
            existing_list.append(
                {
                    "user_phrase": alias.user_phrase,
                    "canonical_term": alias.canonical_term,
                    "confidence": ALIAS_BASE_CONFIDENCE,
                }
            )
            existing_map[key] = len(existing_list) - 1
        changed += 1

    # SQLAlchemy won't detect in-place list mutation — reassign
    profile.aliases = existing_list
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(profile, "aliases")
    db.commit()
    return changed


# ── Public entry point ────────────────────────────────────────────────────────

async def extract_and_merge_aliases(
    *,
    db: Session,
    tenant_id: uuid.UUID,
    cluster_questions_list: list[list[str]],
    api_key: str,
) -> int:
    """Run alias extraction for all qualifying clusters. Returns total aliases created/updated."""
    qualifying = [
        questions
        for questions in cluster_questions_list
        if should_extract_aliases(questions)
    ]
    if not qualifying:
        return 0

    # Fan-out with semaphore guard (max 3 parallel LLM calls enforced inside _call_alias_llm)
    tasks = [_call_alias_llm(questions, api_key) for questions in qualifying]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_new: list[AliasEntry] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Alias LLM call failed: %s", result)
            continue
        all_new.extend(result)

    if not all_new:
        return 0

    return _merge_aliases_into_profile(db, tenant_id, all_new)
