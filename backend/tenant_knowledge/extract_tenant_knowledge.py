from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.models import Document, DocumentType, Embedding, TenantProfile
from backend.tenant_knowledge.faq_service import (
    FAQ_MIN_CONFIDENCE_THRESHOLD,
    insert_new_faq_candidates,
)
from backend.tenant_knowledge.openapi_extractor import extract_openapi_knowledge
from backend.tenant_knowledge.schemas import AliasEntry, FaqCandidate, GlossaryEntry
from backend.tenant_knowledge.tenant_profile_service import merge_into_profile

logger = logging.getLogger(__name__)


def _truncate_to_approx_tokens(text: str, max_tokens: int = 6000) -> str:
    # Repo uses ~4 chars/token for lightweight guarding.
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _count_term_occurrences(haystack: str, needle: str) -> int:
    needle = (needle or "").strip()
    if not needle:
        return 0
    pattern = re.escape(needle)
    return len(re.findall(pattern, haystack, flags=re.IGNORECASE))


def _confidence_from_count(count: int) -> float:
    # Phase 1 spec-inspired normalization: explicit multi → high; single → medium; none → low.
    if count >= 2:
        return 0.9
    if count == 1:
        return 0.6
    return 0.3


def _support_email_and_urls(support_contacts: list[object]) -> tuple[str | None, list[str]]:
    email: str | None = None
    urls: list[str] = []
    for raw in support_contacts:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        if "@" in value and not value.lower().startswith("http"):
            if email is None:
                email = value
            continue
        if value.lower().startswith("http"):
            urls.append(value)
    # dedupe urls
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        key = u.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(u)
    return email, deduped


def _faq_confidence(question: str, combined_text: str) -> float:
    tokens = [t for t in re.findall(r"\w+", question.casefold()) if len(t) >= 4]
    if not tokens:
        return 0.3
    total = 0
    for t in tokens[:8]:
        total += _count_term_occurrences(combined_text, t)
        if total >= 2:
            break
    return _confidence_from_count(2 if total >= 2 else (1 if total == 1 else 0))


def _product_confidence(product_name: str, combined_text: str) -> float:
    return _confidence_from_count(_count_term_occurrences(combined_text, product_name))


def _glossary_confidence(term: str, combined_text: str, definition: str | None) -> float:
    count = _count_term_occurrences(combined_text, term)
    if count >= 2:
        return 0.9
    if count == 1:
        # If we also have a definition, treat as medium rather than noise.
        return 0.6 if definition else 0.3
    return 0.3


def run_extract_client_knowledge_for_document(
    *,
    document_id: uuid.UUID,
    db: Session,
    api_key: str,
) -> None:
    """Extract structured tenant knowledge after successful document embedding.

    Exceptions propagate to the caller. When invoked from an ARQ job the
    @register_job wrapper handles retry bookkeeping and re-raises so ARQ can
    schedule the next attempt or move the row to dead_letter.
    """
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return
        tenant_id = doc.tenant_id

        rows = (
            db.query(Embedding)
            .filter(Embedding.document_id == document_id)
            .filter(Embedding.chunk_text.isnot(None))
            .order_by(func.length(Embedding.chunk_text).desc())
            .limit(40)
            .all()
        )
        chunks = [r.chunk_text for r in rows if r.chunk_text and r.chunk_text.strip()]
        if not chunks:
            logger.info(
                "Tenant knowledge extraction skipped: no chunks "
                "(document_id=%s tenant_id=%s)",
                document_id,
                tenant_id,
            )
            return

        combined_text = _truncate_to_approx_tokens("\n\n".join(chunks), max_tokens=6000)
        logger.info(
            "Tenant knowledge extraction started "
            "(document_id=%s tenant_id=%s file_type=%s embedding_rows=%s chunks_used=%s combined_chars=%s)",
            document_id,
            tenant_id,
            doc.file_type.value,
            len(rows),
            len(chunks),
            len(combined_text),
        )

        openai_client = get_openai_client(api_key)

        system_prompt = (
            "You are a knowledge extraction assistant.\n"
            "Extract structured data ONLY from the provided documentation.\n"
            "Return ONLY what is explicitly stated. Do NOT infer,\n"
            "generalize, or add external knowledge. If a field is absent — return null or [].\n"
        )
        user_prompt = (
            "Extract from the following documentation:\n"
            f"{combined_text}\n"
            "Return JSON with this exact schema:\n"
            "{\n"
            '  "product_name": "string or null",\n'
            '  "topics": ["string"],\n'
            '  "glossary_terms": [{"term": "str", "definition": "str or null"}],\n'
            '  "support_contacts": ["email or URL"],\n'
            '  "faq_candidates": [{"question": "str", "answer": "str"}]\n'
            "}\n"
            "Return ONLY the JSON object. No explanation. No markdown.\n"
        )

        response = openai_client.chat.completions.create(
            model=settings.extraction_model,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw_content = response.choices[0].message.content or "{}"
        import json

        extracted = json.loads(raw_content)
        if not isinstance(extracted, dict):
            logger.warning(
                "Tenant knowledge extraction returned non-dict payload "
                "(document_id=%s tenant_id=%s payload_type=%s)",
                document_id,
                tenant_id,
                type(extracted).__name__,
            )
            return
        raw_faq = extracted.get("faq_candidates") or []
        raw_faq_count = len(raw_faq) if isinstance(raw_faq, list) else 0

        product_name = extracted.get("product_name")
        product_name_norm = product_name.strip() if isinstance(product_name, str) else None

        topics: list[str] = []
        raw_topics = extracted.get("topics")
        if not isinstance(raw_topics, list):
            raw_topics = []
        for m in raw_topics:
            if not isinstance(m, str):
                continue
            value = m.strip()
            if not value:
                continue
            topics.append(value)
        dedup_topics: list[str] = []
        seen_topics: set[str] = set()
        for m in topics:
            key = m.casefold()
            if key in seen_topics:
                continue
            seen_topics.add(key)
            dedup_topics.append(m)
        topics = dedup_topics

        glossary_entries: list[GlossaryEntry] = []
        raw_glossary = extracted.get("glossary_terms") or []
        if isinstance(raw_glossary, list):
            for item in raw_glossary:
                if not isinstance(item, dict):
                    continue
                term = item.get("term")
                definition = item.get("definition")
                if not isinstance(term, str) or not term.strip():
                    continue
                definition_str = definition.strip() if isinstance(definition, str) and definition.strip() else None
                conf = _glossary_confidence(term, combined_text, definition_str)
                glossary_entries.append(
                    GlossaryEntry(
                        term=term.strip(),
                        definition=definition_str,
                        confidence=conf,
                        source="docs",
                    )
                )

        support_contacts = extracted.get("support_contacts") or []
        support_email, support_urls = _support_email_and_urls(
            support_contacts if isinstance(support_contacts, list) else []
        )
        support_email_conf = (
            _confidence_from_count(_count_term_occurrences(combined_text, support_email))
            if support_email
            else 0.0
        )

        faq_candidates: list[FaqCandidate] = []
        faq_skipped_non_dict = 0
        faq_skipped_non_string = 0
        faq_skipped_too_short = 0
        faq_skipped_duplicate_question = 0
        if isinstance(raw_faq, list):
            seen_questions: set[str] = set()
            for item in raw_faq:
                if not isinstance(item, dict):
                    faq_skipped_non_dict += 1
                    continue
                q = item.get("question")
                a = item.get("answer")
                if not isinstance(q, str) or not isinstance(a, str):
                    faq_skipped_non_string += 1
                    continue
                question = q.strip()
                answer = a.strip()
                if len(question) < 10 or len(answer) < 20:
                    faq_skipped_too_short += 1
                    continue
                norm_q = question.casefold()
                if norm_q in seen_questions:
                    faq_skipped_duplicate_question += 1
                    continue
                seen_questions.add(norm_q)
                conf = _faq_confidence(question, combined_text)
                faq_candidates.append(
                    FaqCandidate(
                        question=question,
                        answer=answer,
                        confidence=conf,
                        source="docs",
                    )
                )
        faq_low_confidence = sum(
            1
            for candidate in faq_candidates
            if candidate.confidence is None
            or candidate.confidence < FAQ_MIN_CONFIDENCE_THRESHOLD
        )
        faq_medium_or_high_confidence = len(faq_candidates) - faq_low_confidence
        faq_batch_id = uuid.uuid4()
        logger.info(
            "Tenant knowledge extraction parsed FAQ candidates "
            "(batch_id=%s document_id=%s tenant_id=%s raw_faq_count=%s parsed_faq_count=%s "
            "medium_or_high_confidence=%s low_confidence=%s skipped_non_dict=%s "
            "skipped_non_string=%s skipped_too_short=%s skipped_duplicate_question=%s)",
            faq_batch_id,
            document_id,
            tenant_id,
            raw_faq_count,
            len(faq_candidates),
            faq_medium_or_high_confidence,
            faq_low_confidence,
            faq_skipped_non_dict,
            faq_skipped_non_string,
            faq_skipped_too_short,
            faq_skipped_duplicate_question,
        )
        for candidate in faq_candidates:
            logger.info(
                "Tenant knowledge extraction FAQ candidate "
                "(batch_id=%s document_id=%s tenant_id=%s question=%r confidence=%.3f source=%s)",
                faq_batch_id,
                document_id,
                tenant_id,
                candidate.question,
                float(candidate.confidence),
                candidate.source,
            )

        # Swagger/OpenAPI parsing without extra LLM calls.
        aliases: list[AliasEntry] = []
        if doc.file_type == DocumentType.swagger:
            openapi_topics, openapi_glossary, openapi_aliases = extract_openapi_knowledge(
                swagger_text=combined_text
            )
            # Union topics; glossary merge will prioritize confidence.
            topics = list({m.casefold(): m for m in (topics + openapi_topics)}.values())
            glossary_entries.extend(openapi_glossary)
            aliases = openapi_aliases

        # product_name confidence from occurrences.
        product_conf = (
            _product_confidence(product_name_norm, combined_text)
            if product_name_norm
            else 0.0
        )

        updated_at = datetime.now(UTC)
        merge_into_profile(
            db,
            tenant_id=tenant_id,
            # Preserve extracted casing; only trim/collapse spaces upstream.
            product_name=product_name_norm if product_name_norm else None,
            product_name_confidence=float(product_conf),
            topics=topics,
            glossary_entries=glossary_entries,
            support_email=support_email,
            support_email_confidence=float(support_email_conf),
            support_urls=support_urls,
            escalation_policy=None,
            aliases=aliases,
            updated_at=updated_at,
        )
        logger.info(
            "Tenant knowledge extraction merged profile "
            "(document_id=%s tenant_id=%s product_name_present=%s topics=%s glossary_entries=%s "
            "support_email_present=%s support_urls=%s aliases=%s)",
            document_id,
            tenant_id,
            bool(product_name_norm),
            len(topics),
            len(glossary_entries),
            bool(support_email),
            len(support_urls),
            len(aliases),
        )

        # Insert medium/high-confidence FAQ candidates; low-confidence and duplicates are skipped inside.
        if faq_candidates:
            insert_new_faq_candidates(
                db=db,
                tenant_id=tenant_id,
                faq_candidates=faq_candidates,
                api_key=api_key,
                document_id=document_id,
                batch_id=faq_batch_id,
            )
        else:
            logger.info(
                "Tenant knowledge extraction finished with no FAQ candidates to insert "
                "(batch_id=%s document_id=%s tenant_id=%s)",
                faq_batch_id,
                document_id,
                tenant_id,
            )

    except Exception:
        logger.exception("Tenant knowledge extraction failed for document_id=%s", document_id)
        try:
            profile = db.get(TenantProfile, tenant_id)
            if profile is not None:
                profile.extraction_status = "failed"
                db.add(profile)
                db.commit()
        except Exception:
            db.rollback()
        raise
