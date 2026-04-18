"""DB dialect capabilities + enum/value helpers shared across _repo submodules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.gap_analyzer.enums import GapClusterStatus, GapDocTopicStatus, GapJobStatus


@dataclass(frozen=True)
class _RepositoryCapabilities:
    enum_values_as_strings: bool
    supports_array_values: bool
    supports_skip_locked: bool


def _repository_capabilities(db: Session) -> _RepositoryCapabilities:
    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    return _RepositoryCapabilities(
        enum_values_as_strings=dialect_name == "sqlite",
        supports_array_values=dialect_name != "sqlite",
        supports_skip_locked=dialect_name == "postgresql",
    )


def _enum_value(
    value: GapClusterStatus | GapDocTopicStatus | GapJobStatus,
    *,
    capabilities: _RepositoryCapabilities,
) -> str | GapClusterStatus | GapDocTopicStatus | GapJobStatus:
    if capabilities.enum_values_as_strings:
        return value.value
    return value


def _example_questions_value(
    value: list[str],
    *,
    capabilities: _RepositoryCapabilities,
) -> object:
    if capabilities.supports_array_values:
        return value
    return None


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
