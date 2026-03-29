"""LLM adjudication helpers for deterministic contradiction facts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal, Sequence

from backend.core.openai_client import get_openai_client

logger = logging.getLogger(__name__)

ContradictionAdjudicationVerdict = Literal["confirmed", "rejected", "inconclusive"]
ContradictionAdjudicationStatus = Literal[
    "disabled",
    "skipped_no_candidates",
    "skipped_global_config",
    "skipped_client_setting",
    "skipped_missing_client_key",
    "completed",
    "completed_with_errors",
    "failed_open",
]


@dataclass(frozen=True)
class ContradictionAdjudication:
    """One adjudication payload for one deterministic contradiction fact."""

    verdict: ContradictionAdjudicationVerdict | None = None
    rationale: str | None = None
    model: str | None = None
    skip_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ContradictionAdjudicationCandidate:
    """Compact, stable input for one contradiction fact adjudication."""

    fact_id: str
    chunk_a_id: str
    chunk_b_id: str
    basis: str
    value_a: str
    value_b: str
    preview_a: str
    preview_b: str
    metadata_a: dict[str, object] = field(default_factory=dict)
    metadata_b: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FactAdjudicationResult:
    """One adjudication result keyed by deterministic fact id."""

    fact_id: str
    adjudication: ContradictionAdjudication


@dataclass(frozen=True)
class ContradictionAdjudicationRun:
    """Whole-run adjudication summary plus per-fact results."""

    enabled: bool = False
    applied_to_any_fact: bool = False
    status: ContradictionAdjudicationStatus = "disabled"
    candidate_count: int = 0
    sent_count: int = 0
    completed_count: int = 0
    confirmed_count: int = 0
    rejected_count: int = 0
    inconclusive_count: int = 0
    error_count: int = 0
    model: str | None = None
    items: tuple[FactAdjudicationResult, ...] = ()


def serialize_contradiction_adjudication(
    adjudication: ContradictionAdjudication,
) -> dict[str, object]:
    return {
        "verdict": adjudication.verdict,
        "rationale": adjudication.rationale,
        "model": adjudication.model,
        "skip_reason": adjudication.skip_reason,
        "error": adjudication.error,
    }


def serialize_fact_adjudication_result(
    result: FactAdjudicationResult,
) -> dict[str, object]:
    return {
        "fact_id": result.fact_id,
        "adjudication": serialize_contradiction_adjudication(result.adjudication),
    }


def serialize_contradiction_adjudication_run(
    run: ContradictionAdjudicationRun,
) -> dict[str, object]:
    return {
        "enabled": run.enabled,
        "applied_to_any_fact": run.applied_to_any_fact,
        "status": run.status,
        "candidate_count": run.candidate_count,
        "sent_count": run.sent_count,
        "completed_count": run.completed_count,
        "confirmed_count": run.confirmed_count,
        "rejected_count": run.rejected_count,
        "inconclusive_count": run.inconclusive_count,
        "error_count": run.error_count,
        "model": run.model,
        "items": [serialize_fact_adjudication_result(item) for item in run.items],
    }


def build_contradiction_adjudication_run(
    *,
    enabled: bool,
    status: ContradictionAdjudicationStatus,
    candidate_count: int,
    sent_count: int = 0,
    completed_count: int = 0,
    confirmed_count: int = 0,
    rejected_count: int = 0,
    inconclusive_count: int = 0,
    error_count: int = 0,
    model: str | None = None,
    items: Sequence[FactAdjudicationResult] = (),
) -> ContradictionAdjudicationRun:
    return ContradictionAdjudicationRun(
        enabled=enabled,
        applied_to_any_fact=completed_count > 0,
        status=status,
        candidate_count=candidate_count,
        sent_count=sent_count,
        completed_count=completed_count,
        confirmed_count=confirmed_count,
        rejected_count=rejected_count,
        inconclusive_count=inconclusive_count,
        error_count=error_count,
        model=model,
        items=tuple(items),
    )


def _truncate_preview(text: str, *, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    if limit <= 3:
        return compact[:limit]
    return compact[: limit - 3] + "..."


def _build_prompt(candidates: Sequence[ContradictionAdjudicationCandidate]) -> str:
    items_payload = [
        {
            "fact_id": candidate.fact_id,
            "chunk_pair": {
                "chunk_a_id": candidate.chunk_a_id,
                "chunk_b_id": candidate.chunk_b_id,
            },
            "fact": {
                "basis": candidate.basis,
                "value_a": candidate.value_a,
                "value_b": candidate.value_b,
            },
            "preview_a": candidate.preview_a,
            "preview_b": candidate.preview_b,
            "metadata_a": candidate.metadata_a,
            "metadata_b": candidate.metadata_b,
        }
        for candidate in candidates
    ]
    return (
        "Classify contradiction facts only from the supplied evidence.\n"
        "Return JSON only with shape "
        '{"items":[{"fact_id":"...","verdict":"confirmed|rejected|inconclusive","rationale":"..."}]}.\n'
        "Rules:\n"
        "- Do not use any external knowledge.\n"
        "- Do not infer beyond the supplied fact, previews, and metadata.\n"
        "- rationale is optional and must be at most 1-2 short sentences.\n"
        "- Do not quote long excerpts.\n"
        "- If evidence is insufficient, use inconclusive.\n\n"
        f"Facts:\n{json.dumps(items_payload, ensure_ascii=True)}"
    )


def adjudicate_contradictions(
    candidates: Sequence[ContradictionAdjudicationCandidate],
    *,
    api_key: str,
    model: str,
    max_facts: int,
    preview_chars: int,
    max_tokens: int,
) -> ContradictionAdjudicationRun:
    """Adjudicate the first N contradiction facts in one JSON-only request."""

    candidate_count = len(candidates)
    if candidate_count == 0:
        return build_contradiction_adjudication_run(
            enabled=True,
            status="skipped_no_candidates",
            candidate_count=0,
            model=model,
        )
    if not api_key:
        return build_contradiction_adjudication_run(
            enabled=True,
            status="skipped_missing_client_key",
            candidate_count=candidate_count,
            model=model,
        )

    sent_candidates = [
        ContradictionAdjudicationCandidate(
            fact_id=candidate.fact_id,
            chunk_a_id=candidate.chunk_a_id,
            chunk_b_id=candidate.chunk_b_id,
            basis=candidate.basis,
            value_a=candidate.value_a,
            value_b=candidate.value_b,
            preview_a=_truncate_preview(candidate.preview_a, limit=preview_chars),
            preview_b=_truncate_preview(candidate.preview_b, limit=preview_chars),
            metadata_a=dict(candidate.metadata_a),
            metadata_b=dict(candidate.metadata_b),
        )
        for candidate in candidates[:max_facts]
    ]

    started_at = perf_counter()
    try:
        client = get_openai_client(api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _build_prompt(sent_candidates)}],
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw_content = response.choices[0].message.content or ""
        parsed = json.loads(raw_content)
    except Exception as exc:
        logger.warning("Contradiction adjudication failed open: %s", exc)
        return build_contradiction_adjudication_run(
            enabled=True,
            status="failed_open",
            candidate_count=candidate_count,
            sent_count=len(sent_candidates),
            error_count=len(sent_candidates),
            model=model,
            items=[
                FactAdjudicationResult(
                    fact_id=candidate.fact_id,
                    adjudication=ContradictionAdjudication(
                        model=model,
                        error=str(exc),
                    ),
                )
                for candidate in sent_candidates
            ],
        )

    items_payload = parsed.get("items")
    if not isinstance(items_payload, list):
        error = "invalid_items_payload"
        return build_contradiction_adjudication_run(
            enabled=True,
            status="failed_open",
            candidate_count=candidate_count,
            sent_count=len(sent_candidates),
            error_count=len(sent_candidates),
            model=model,
            items=[
                FactAdjudicationResult(
                    fact_id=candidate.fact_id,
                    adjudication=ContradictionAdjudication(
                        model=model,
                        error=error,
                    ),
                )
                for candidate in sent_candidates
            ],
        )

    results_by_fact_id: dict[str, ContradictionAdjudication] = {}
    error_count = 0

    for item in items_payload:
        if not isinstance(item, dict):
            error_count += 1
            continue
        fact_id = item.get("fact_id")
        verdict = item.get("verdict")
        if not isinstance(fact_id, str) or not fact_id.strip():
            error_count += 1
            continue
        if verdict not in {"confirmed", "rejected", "inconclusive"}:
            results_by_fact_id[fact_id] = ContradictionAdjudication(
                model=model,
                error="invalid_verdict",
            )
            error_count += 1
            continue
        rationale = item.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            rationale = None
        if isinstance(rationale, str):
            rationale = _truncate_preview(rationale, limit=220)
        results_by_fact_id[fact_id] = ContradictionAdjudication(
            verdict=verdict,
            rationale=rationale,
            model=model,
        )

    results: list[FactAdjudicationResult] = []
    confirmed_count = 0
    rejected_count = 0
    inconclusive_count = 0

    for candidate in sent_candidates:
        adjudication = results_by_fact_id.get(candidate.fact_id)
        if adjudication is None:
            adjudication = ContradictionAdjudication(
                model=model,
                error="missing_fact_result",
            )
            error_count += 1
        if adjudication.verdict == "confirmed":
            confirmed_count += 1
        elif adjudication.verdict == "rejected":
            rejected_count += 1
        elif adjudication.verdict == "inconclusive":
            inconclusive_count += 1
        results.append(
            FactAdjudicationResult(
                fact_id=candidate.fact_id,
                adjudication=adjudication,
            )
        )

    duration_ms = round((perf_counter() - started_at) * 1000, 2)
    if error_count > 0:
        logger.warning(
            "Contradiction adjudication completed with errors: sent=%s errors=%s duration_ms=%s",
            len(sent_candidates),
            error_count,
            duration_ms,
        )
        status: ContradictionAdjudicationStatus = "completed_with_errors"
    else:
        status = "completed"

    return build_contradiction_adjudication_run(
        enabled=True,
        status=status,
        candidate_count=candidate_count,
        sent_count=len(sent_candidates),
        completed_count=confirmed_count + rejected_count + inconclusive_count,
        confirmed_count=confirmed_count,
        rejected_count=rejected_count,
        inconclusive_count=inconclusive_count,
        error_count=error_count,
        model=model,
        items=results,
    )
