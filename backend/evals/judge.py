"""LLM-as-judge backed by Anthropic Claude.

Anthropic is used here on purpose: the chat backend itself runs on
OpenAI, and a same-family judge can systematically agree with same-
family answers. An independent Claude model is a cheap way to break
that bias.

Judges return a numeric score in [0.0, 1.0] plus a short rationale. A
case with no ``judge_rubric`` skips the LLM call.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from backend.evals.dataset import GoldenCase

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "claude-haiku-4-5"

_SYSTEM_PROMPT = """You are an impartial evaluator scoring a chatbot's answer
against a rubric. Be terse and strict. Reply with a single JSON object only,
no prose around it:

{"score": <float 0.0-1.0>, "rationale": "<one short sentence>"}

Score 1.0 = fully satisfies the rubric. 0.0 = ignores or contradicts it.
Reward concrete, on-topic answers; penalise vagueness, hallucinated facts,
or off-topic content."""

_USER_TEMPLATE = """Rubric:
{rubric}

User question (lang={lang}):
{question}

Bot answer:
{answer}

Return JSON only."""


@dataclass(frozen=True)
class JudgeResult:
    score: float
    rationale: str
    model: str


class AnthropicJudge:
    """Stateless wrapper that grades a single (case, output) pair."""

    def __init__(
        self,
        model: str = DEFAULT_JUDGE_MODEL,
        api_key: str | None = None,
        max_tokens: int = 256,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set; pass via env or --judge-api-key."
            )
        # Lazy import: the SDK is only required when the judge is actually
        # constructed, so unit tests can exercise the parser without it.
        from anthropic import Anthropic

        self._client = Anthropic(api_key=key)

    def grade(self, case: GoldenCase, output: str) -> JudgeResult | None:
        if not case.judge_rubric:
            return None
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _USER_TEMPLATE.format(
                        rubric=case.judge_rubric.strip(),
                        lang=case.lang,
                        question=" | ".join(case.messages),
                        answer=output,
                    ),
                }
            ],
        )
        raw = message.content[0].text if message.content else ""
        return _parse_judge_response(raw, self.model)


def _parse_judge_response(raw: str, model: str) -> JudgeResult:
    """Extract JSON from the judge's reply. Tolerates leading/trailing prose
    by grabbing the first ``{...}`` block."""

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        logger.warning("judge returned non-JSON response: %r", raw[:200])
        return JudgeResult(score=0.0, rationale=f"unparseable: {raw[:120]!r}", model=model)
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("judge JSON decode failed: %s", exc)
        return JudgeResult(score=0.0, rationale=f"invalid json: {exc}", model=model)
    score = float(parsed.get("score", 0.0))
    score = max(0.0, min(1.0, score))
    rationale = str(parsed.get("rationale", "")).strip()
    return JudgeResult(score=score, rationale=rationale, model=model)
