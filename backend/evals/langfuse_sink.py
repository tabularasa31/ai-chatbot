"""Optional Langfuse persistence for eval datasets and runs.

When ``LANGFUSE_HOST`` / ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``
are set, the runner can mirror golden datasets and per-run results into
Langfuse so the team can browse runs in the UI, diff tags, and link
eval traces to production traces.

Without those env vars every function here is a logged no-op — the
runner stays usable in dev with no extra setup.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.evals.dataset import Dataset
from backend.evals.report import RunReport

logger = logging.getLogger(__name__)


def _client_or_none() -> Any | None:
    """Return a configured Langfuse client, or ``None`` if creds are
    not in the environment. The langfuse package is imported lazily so
    importing this module does not require it."""

    host = os.environ.get("LANGFUSE_HOST")
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (host and pub and sec):
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("langfuse SDK not installed; skipping upload")
        return None
    return Langfuse(host=host, public_key=pub, secret_key=sec)


def upload_dataset(dataset: Dataset, *, client: Any | None = None) -> bool:
    """Mirror ``dataset`` into Langfuse as a Dataset + items.

    Idempotent: re-running with the same dataset upserts items by their
    ``id`` so the dataset stays in sync with the YAML on disk.

    Returns ``True`` if anything was uploaded, ``False`` if Langfuse is
    not configured.
    """

    client = client or _client_or_none()
    if client is None:
        logger.info("langfuse_dataset_upload_skipped reason=no_creds dataset=%s", dataset.name)
        return False

    try:
        client.create_dataset(name=dataset.name, description=dataset.description or None)
    except Exception:
        # create_dataset is "create-or-noop" in Langfuse — tolerate any
        # error here (existing dataset, race) and proceed to items.
        logger.debug("langfuse_create_dataset_noop name=%s", dataset.name, exc_info=True)

    for case in dataset.cases:
        try:
            client.create_dataset_item(
                dataset_name=dataset.name,
                id=case.id,
                input={"question": case.input, "lang": case.lang},
                expected_output={"judge_rubric": case.judge_rubric} if case.judge_rubric else None,
                metadata={
                    "category": case.category,
                    "must_contain": case.must_contain,
                    "must_not_contain": case.must_not_contain,
                    "expected_lang": case.expected_lang,
                },
            )
        except Exception:
            logger.warning("langfuse_dataset_item_failed id=%s", case.id, exc_info=True)

    try:
        client.flush()
    except Exception:
        logger.debug("langfuse_flush_failed", exc_info=True)

    logger.info("langfuse_dataset_uploaded name=%s items=%d", dataset.name, len(dataset.cases))
    return True


def upload_run(report: RunReport, *, client: Any | None = None) -> bool:
    """Persist every case of ``report`` as a Langfuse trace, with
    scores attached so the UI can diff runs by tag.

    Each case becomes one trace, tagged with ``report.tag`` and the
    case's ``category``. Scores written:

    - ``overall_passed`` (BOOLEAN): the case's pass/fail verdict
    - ``deterministic_passed`` (BOOLEAN): all hard checks satisfied
    - ``judge_score`` (NUMERIC): only when the judge ran on the case

    Returns ``True`` if traces were uploaded, ``False`` if Langfuse is
    not configured.
    """

    client = client or _client_or_none()
    if client is None:
        logger.info(
            "langfuse_run_upload_skipped reason=no_creds dataset=%s tag=%s",
            report.dataset,
            report.tag,
        )
        return False

    for case in report.cases:
        try:
            trace = client.trace(
                name=f"eval/{report.dataset}/{case.case_id}",
                tags=[f"run:{report.tag}", f"category:{case.category}", "evals"],
                input=case.input,
                output=case.output,
                metadata={
                    "case_id": case.case_id,
                    "category": case.category,
                    "lang": case.lang,
                    "latency_ms": case.latency_ms,
                    "bot_public_id": report.bot_public_id,
                    "judge_model": report.judge_model,
                    "error": case.error,
                },
            )
            trace_id = getattr(trace, "id", None) or getattr(trace, "trace_id", None)

            client.score(
                trace_id=trace_id,
                name="overall_passed",
                value=1.0 if case.overall_passed else 0.0,
                data_type="BOOLEAN",
            )
            client.score(
                trace_id=trace_id,
                name="deterministic_passed",
                value=1.0 if case.deterministic_passed else 0.0,
                data_type="BOOLEAN",
            )
            if case.judge is not None:
                client.score(
                    trace_id=trace_id,
                    name="judge_score",
                    value=float(case.judge.score),
                    data_type="NUMERIC",
                    comment=case.judge.rationale or None,
                )
        except Exception:
            logger.warning("langfuse_run_trace_failed id=%s", case.case_id, exc_info=True)

    try:
        client.flush()
    except Exception:
        logger.debug("langfuse_flush_failed", exc_info=True)

    logger.info(
        "langfuse_run_uploaded dataset=%s tag=%s cases=%d",
        report.dataset,
        report.tag,
        len(report.cases),
    )
    return True
