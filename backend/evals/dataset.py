"""Golden dataset loader and schema.

Datasets live under ``tests/eval/datasets/*.yaml`` and are version
controlled in git so reviewers can diff additions to the dataset in
the same PR that changes prompts or retrieval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

Category = Literal[
    "happy_path",
    "rag",
    "guards",
    "multilingual",
    "golden_scenario",
    "loop_detection",
]
Language = Literal["ru", "en", "es", "de", "fr", "it", "pt", "uk", "any"]


class GoldenCase(BaseModel):
    """One eval case. Single-turn cases set ``input``; multi-turn cases
    set ``turns`` (a list of user messages sent in one chat session)."""

    id: str
    category: Category
    lang: Language = "any"
    input: str | None = None
    """Single-turn user message. Mutually exclusive with ``turns``."""

    turns: list[str] | None = None
    """Multi-turn chain — user messages sent sequentially in a single
    chat session. Deterministic metrics and the LLM judge run against
    the *final* turn's output; per-turn outputs are recorded for
    transcript inspection. Mutually exclusive with ``input``."""

    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)

    expected_lang: Language | None = None
    """Language the bot is expected to reply in. Defaults to ``lang``
    when not set; pass ``any`` to disable the check."""

    expected_escalation_offered_by_turn: int | None = None
    """For chain cases — assert the bot offered escalation by this turn
    (1-indexed). ``None`` disables the assertion. Use ``-1`` to assert
    that escalation must NOT be offered at any turn (control cases)."""

    judge_rubric: str | None = None
    """Free-form rubric passed to the Anthropic judge. When omitted,
    the case is only scored by deterministic metrics."""

    @field_validator("id")
    @classmethod
    def _id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def _input_xor_turns(self) -> GoldenCase:
        has_input = bool(self.input and self.input.strip())
        has_turns = bool(self.turns)
        if has_input == has_turns:
            raise ValueError("exactly one of `input` or `turns` must be set")
        if has_turns and any(not (t and t.strip()) for t in self.turns or []):
            raise ValueError("every entry in `turns` must be a non-empty string")
        return self

    @property
    def messages(self) -> list[str]:
        """Normalised list of user messages for the runner — works for
        both single-turn (``input``) and multi-turn (``turns``) cases."""
        if self.turns:
            return list(self.turns)
        assert self.input is not None
        return [self.input]


class Dataset(BaseModel):
    """A versioned, named collection of golden cases."""

    name: str
    description: str = ""
    cases: list[GoldenCase]

    @field_validator("cases")
    @classmethod
    def _ids_unique(cls, v: list[GoldenCase]) -> list[GoldenCase]:
        seen: set[str] = set()
        for case in v:
            if case.id in seen:
                raise ValueError(f"duplicate case id: {case.id}")
            seen.add(case.id)
        return v


def load_dataset(path: str | Path) -> Dataset:
    """Parse a YAML dataset file. Raises ``pydantic.ValidationError``
    on schema violations and ``FileNotFoundError`` if the path is
    missing."""

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Dataset.model_validate(raw)


def discover_datasets(root: str | Path) -> list[Path]:
    """Return all ``*.yaml`` files under ``root`` (non-recursive)."""

    return sorted(Path(root).glob("*.yaml"))
