from __future__ import annotations

import logging

from backend.core.openai_client import get_openai_client

logger = logging.getLogger(__name__)

LOCALIZATION_MODEL = "gpt-4o-mini"


def localize_text_to_question_language(
    *,
    canonical_text: str,
    question: str | None,
    api_key: str | None,
) -> str:
    if not canonical_text.strip():
        return canonical_text

    question_text = (question or "").strip()
    if not question_text or not api_key:
        return canonical_text

    try:
        client = get_openai_client(api_key)
        response = client.chat.completions.create(
            model=LOCALIZATION_MODEL,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You localize assistant messages. Rewrite the assistant message in the same "
                        "language as the user's question. Preserve meaning, tone, product names, "
                        "module names, placeholders, and ticket tokens exactly. Return only the "
                        "localized assistant message."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User question:\n{question_text}\n\n"
                        f"Assistant message to localize:\n{canonical_text}"
                    ),
                },
            ],
        )
        localized = (response.choices[0].message.content or "").strip()
        return localized or canonical_text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Localization failed; using canonical text: %s", exc)
        return canonical_text
