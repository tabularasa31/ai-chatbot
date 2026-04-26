"""LLM prompts and embedding helpers for Gap Analyzer."""

from __future__ import annotations

import json
from dataclasses import dataclass

from backend.core.config import settings
from backend.core.openai_client import get_openai_client


@dataclass(frozen=True)
class ModeATopicCandidate:
    topic_label: str
    example_questions: list[str]


def extract_mode_a_candidates(
    *,
    encrypted_api_key: str,
    sampled_chunks: list[str],
) -> list[ModeATopicCandidate]:
    if not sampled_chunks:
        return []

    openai_client = get_openai_client(encrypted_api_key)
    system_prompt = (
        "You analyze documentation excerpts and identify likely documentation gaps.\n"
        "Return ONLY topics that appear under-documented or missing for end users.\n"
        "Do not return topics that are already clearly covered.\n"
        "Return ONLY JSON."
    )
    user_prompt = (
        "Review the following sampled documentation excerpts.\n"
        "Suggest up to 8 end-user-facing topics that still look missing or weakly covered.\n"
        "For each topic, include 1-3 example user questions.\n"
        "Return JSON with this exact schema:\n"
        '{ "topics": [ { "topic_label": "string", "example_questions": ["string"] } ] }\n'
        "Documentation sample:\n"
        + "\n\n---\n\n".join(sampled_chunks)
    )

    response = openai_client.chat.completions.create(
        model=settings.extraction_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw_content = response.choices[0].message.content or "{}"
    parsed = json.loads(raw_content)
    raw_topics = parsed.get("topics") if isinstance(parsed, dict) else []
    if not isinstance(raw_topics, list):
        return []

    candidates: list[ModeATopicCandidate] = []
    for item in raw_topics:
        if not isinstance(item, dict):
            continue
        topic_label = item.get("topic_label")
        if not isinstance(topic_label, str) or not topic_label.strip():
            continue
        raw_questions = item.get("example_questions")
        example_questions = []
        if isinstance(raw_questions, list):
            for question in raw_questions[:3]:
                if isinstance(question, str) and question.strip():
                    example_questions.append(question.strip())
        candidates.append(
            ModeATopicCandidate(
                topic_label=topic_label.strip(),
                example_questions=example_questions,
            )
        )
    return candidates[:8]


def embed_texts(
    *,
    encrypted_api_key: str,
    texts: list[str],
) -> list[list[float]]:
    normalized = [text.strip() for text in texts if text.strip()]
    if not normalized:
        return []

    openai_client = get_openai_client(encrypted_api_key)
    response = openai_client.embeddings.create(
        model=settings.embedding_model,
        input=normalized,
    )
    vectors: list[list[float]] = []
    for item in response.data:
        embedding = getattr(item, "embedding", None)
        if isinstance(embedding, list):
            vectors.append([float(value) for value in embedding])
    return vectors
