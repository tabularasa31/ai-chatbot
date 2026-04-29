"""Retrieval metrics for the multi-hop baseline harness.

Three metrics, all computed against a set of gold chunk_ids:

- ``recall_at_k``: fraction of gold ids found in the top-k. For multi-hop
  queries with several gold chunks this is "did we surface the union?".
  Always in [0, 1].
- ``mrr``: reciprocal rank of the *first* gold id found in the ranking
  (1 if rank 1, 1/2 if rank 2, ..., 0 if not present). Sensitive to where
  the first relevant chunk lands, so it complements recall@k.
- ``precision_at_k``: fraction of the top-k that are gold. For single-gold
  queries this caps at 1/k; for multi-hop with N gold it caps at min(N, k)/k.

We also report **per-category** averages because the entity-overlap
channel is expected to lift multi-hop / brand-specific while not
regressing the control set.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CaseResult:
    query_id: str
    category: str
    gold: tuple[str, ...]
    ranking: tuple[str, ...]  # top-k chunk_ids in order
    recall_at_k: float
    mrr: float
    precision_at_k: float


@dataclass
class CategoryStats:
    category: str
    n: int = 0
    recall_at_k: float = 0.0
    mrr: float = 0.0
    precision_at_k: float = 0.0


@dataclass
class EvalReport:
    k: int
    overall: CategoryStats = field(default_factory=lambda: CategoryStats("overall"))
    per_category: dict[str, CategoryStats] = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)


def evaluate_case(
    *,
    query_id: str,
    category: str,
    gold: tuple[str, ...],
    ranking: list[str],
    k: int,
) -> CaseResult:
    """Compute the three metrics for a single query."""
    top_k = ranking[:k]
    gold_set = set(gold)

    if not gold_set:
        # No gold defined → degenerate case, treat as perfect to avoid skewing.
        return CaseResult(
            query_id=query_id,
            category=category,
            gold=gold,
            ranking=tuple(top_k),
            recall_at_k=1.0,
            mrr=1.0,
            precision_at_k=1.0,
        )

    hits_in_top_k = [c for c in top_k if c in gold_set]
    recall = len(set(hits_in_top_k)) / len(gold_set)

    # MRR uses the FULL ranking (not just top-k) because the conventional
    # definition of MRR doesn't truncate. We evaluate over `ranking` as
    # provided; callers can pass any length they want.
    mrr = 0.0
    for rank, chunk_id in enumerate(ranking, start=1):
        if chunk_id in gold_set:
            mrr = 1.0 / rank
            break

    precision = len(set(hits_in_top_k)) / k

    return CaseResult(
        query_id=query_id,
        category=category,
        gold=gold,
        ranking=tuple(top_k),
        recall_at_k=recall,
        mrr=mrr,
        precision_at_k=precision,
    )


def aggregate(cases: list[CaseResult], *, k: int) -> EvalReport:
    """Macro-average metrics overall and per category."""
    report = EvalReport(k=k)
    report.cases = list(cases)
    if not cases:
        return report

    by_cat: dict[str, list[CaseResult]] = {}
    for c in cases:
        by_cat.setdefault(c.category, []).append(c)

    def _avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    report.overall = CategoryStats(
        category="overall",
        n=len(cases),
        recall_at_k=_avg([c.recall_at_k for c in cases]),
        mrr=_avg([c.mrr for c in cases]),
        precision_at_k=_avg([c.precision_at_k for c in cases]),
    )
    for cat, group in by_cat.items():
        report.per_category[cat] = CategoryStats(
            category=cat,
            n=len(group),
            recall_at_k=_avg([c.recall_at_k for c in group]),
            mrr=_avg([c.mrr for c in group]),
            precision_at_k=_avg([c.precision_at_k for c in group]),
        )
    return report


def format_report(report: EvalReport, *, header: str = "Multi-hop eval baseline") -> str:
    """Render an EvalReport as a fixed-width text block."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"{header}  (k={report.k})")
    lines.append("=" * 72)
    lines.append(
        f"{'category':<22} {'n':>3}  "
        f"{'recall@k':>10} {'mrr':>10} {'prec@k':>10}"
    )
    lines.append("-" * 72)

    def _row(stats: CategoryStats) -> str:
        return (
            f"{stats.category:<22} {stats.n:>3}  "
            f"{stats.recall_at_k:>10.3f} {stats.mrr:>10.3f} "
            f"{stats.precision_at_k:>10.3f}"
        )

    # Stable category order: overall first, then declared category order.
    lines.append(_row(report.overall))
    for cat in (
        "multi_hop",
        "brand_specific",
        "error_or_endpoint",
        "control_no_entities",
    ):
        if cat in report.per_category:
            lines.append(_row(report.per_category[cat]))

    lines.append("=" * 72)
    return "\n".join(lines)
