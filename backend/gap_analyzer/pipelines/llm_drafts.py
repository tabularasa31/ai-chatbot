"""LLM-backed FAQ draft generation for Mode B gap clusters.

Generate / refine paths are sync in V1; an SSE-streaming variant lands in E2.
The output is always a structured ``DraftPayload`` (title, question, markdown)
in the tenant's preferred language. Generation never publishes — the draft is
stored on the cluster and only an explicit admin click promotes it into
``tenant_faq``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from backend.core.config import settings
from backend.core.openai_client import get_openai_client

logger = logging.getLogger(__name__)


_FAQ_DRAFT_SYSTEM = (
    "You write FAQ articles for a product support knowledge base.\n"
    "Given a topic label and example customer questions from real chat logs, "
    "produce one FAQ entry that answers the cluster of questions.\n"
    "Rules:\n"
    "- Output strictly JSON with keys: title, question, markdown.\n"
    "- title: short noun phrase, sentence case, no trailing punctuation.\n"
    "- question: a single canonical user question phrasing that covers the cluster.\n"
    "- markdown: the answer body as GitHub-flavored Markdown. Start with a 1-2 sentence "
    "direct answer, then add details, examples, or steps as needed. Do NOT include the "
    "title or question inside the markdown body.\n"
    "- Write in the requested output language.\n"
    "- Stay factual. If specific facts are not implied by the questions, write generic "
    "guidance that a support admin can fill in. Never invent product specifics."
)


_FAQ_REFINE_SYSTEM = (
    "You are revising an existing FAQ article based on the admin's guidance.\n"
    "Preserve the article's intent; apply the guidance precisely.\n"
    "Rules:\n"
    "- Output strictly JSON with keys: title, question, markdown.\n"
    "- Keep the same output language as the existing draft unless the guidance asks to switch.\n"
    "- Do not introduce facts that contradict the original or the customer questions."
)


@dataclass(frozen=True)
class DraftContent:
    """Plain LLM output. The orchestrator wraps it with persistence metadata."""

    title: str
    question: str
    markdown: str


def _normalize_language(language: str | None) -> str:
    cleaned = (language or "").strip().lower()
    return cleaned or "en"


def _format_questions(questions: list[str]) -> str:
    cleaned = [q.strip() for q in questions if q and q.strip()]
    if not cleaned:
        return "(no example questions available)"
    return "\n".join(f"- {q}" for q in cleaned[:10])


def _parse_draft_payload(raw: str) -> DraftContent:
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("LLM draft response was not a JSON object")
    title = (parsed.get("title") or "").strip()
    question = (parsed.get("question") or "").strip()
    markdown = (parsed.get("markdown") or "").strip()
    if not title or not question or not markdown:
        raise ValueError("LLM draft response missing required fields (title/question/markdown)")
    return DraftContent(title=title, question=question, markdown=markdown)


def generate_draft(
    *,
    encrypted_api_key: str,
    label: str,
    example_questions: list[str],
    coverage_score: float | None,
    signal_weight: float | None,
    language: str | None,
) -> DraftContent:
    """Generate a fresh FAQ draft for a Mode B gap cluster.

    The caller is expected to apply the injection guard to the returned markdown
    and to persist the result. This function does no I/O beyond the OpenAI call.
    """
    output_language = _normalize_language(language)
    client = get_openai_client(encrypted_api_key, timeout=30.0)
    user_prompt = (
        f"Topic label: {label}\n"
        f"Coverage score: {coverage_score if coverage_score is not None else 'unknown'}\n"
        f"Aggregate signal weight: {signal_weight if signal_weight is not None else 'unknown'}\n"
        f"Example customer questions (verbatim from logs):\n{_format_questions(example_questions)}\n\n"
        f"Output language: {output_language}\n"
        "Return JSON with title, question, markdown."
    )
    response = client.chat.completions.create(
        model=settings.extraction_model,
        messages=[
            {"role": "system", "content": _FAQ_DRAFT_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw_content = response.choices[0].message.content or "{}"
    return _parse_draft_payload(raw_content)


def refine_draft(
    *,
    encrypted_api_key: str,
    current_title: str,
    current_question: str,
    current_markdown: str,
    guidance: str,
    label: str,
    example_questions: list[str],
    language: str | None,
) -> DraftContent:
    """Revise an existing draft according to the admin's guidance."""
    output_language = _normalize_language(language)
    client = get_openai_client(encrypted_api_key, timeout=30.0)
    user_prompt = (
        f"Cluster topic: {label}\n"
        f"Example customer questions:\n{_format_questions(example_questions)}\n\n"
        f"Current draft (output language: {output_language}):\n"
        f"Title: {current_title}\n"
        f"Question: {current_question}\n"
        f"Markdown:\n{current_markdown}\n\n"
        f"Admin guidance: {guidance.strip()}\n\n"
        "Return JSON with title, question, markdown."
    )
    response = client.chat.completions.create(
        model=settings.extraction_model,
        messages=[
            {"role": "system", "content": _FAQ_REFINE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw_content = response.choices[0].message.content or "{}"
    return _parse_draft_payload(raw_content)
