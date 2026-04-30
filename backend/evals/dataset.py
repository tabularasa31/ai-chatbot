"""Golden dataset loader and schema.

Datasets live under ``tests/eval/datasets/*.yaml`` and are version
controlled in git so reviewers can diff additions to the dataset in
the same PR that changes prompts or retrieval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Category = Literal[
    "happy_path",
    "rag",
    "guards",
    "multilingual",
    "golden_scenario",
]
Language = Literal["ru", "en", "es", "de", "fr", "it", "pt", "uk", "any"]


class GoldenCase(BaseModel):
    """One eval case. ``input`` is the user message; everything else is
    optional and only the fields that are set are scored."""

    id: str
    category: Category
    lang: Language = "any"
    input: str

    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)

    expected_lang: Language | None = None
    """Language the bot is expected to reply in. Defaults to ``lang``
    when not set; pass ``any`` to disable the check."""

    judge_rubric: str | None = None
    """Free-form rubric passed to the Anthropic judge. When omitted,
    the case is only scored by deterministic metrics."""

    @field_validator("id")
    @classmethod
    def _id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id must not be empty")
        return v.strip()


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
