"""Gap classification and cluster status helpers shared across gap_analyzer modules.

No I/O, no SQLAlchemy, no side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from backend.gap_analyzer.domain import CoveragePolicy, GapLifecyclePolicy
from backend.gap_analyzer.enums import GapClusterStatus
from backend.gap_analyzer.schemas import ModeBStatusFilter

if TYPE_CHECKING:
    from backend.models import GapCluster


def _classify_gap(coverage_score: float | None) -> str:
    if coverage_score is None:
        return "unknown"
    coverage_policy = CoveragePolicy()
    if coverage_score >= coverage_policy.covered_threshold:
        return "covered"
    if coverage_score >= coverage_policy.mode_b_uncovered:
        return "partial"
    return "uncovered"


def _impact_statement(*, total_active: int, uncovered_count: int, partial_count: int) -> str:
    if total_active == 0:
        return "No active gaps detected."
    if uncovered_count > 0:
        noun = "gap" if uncovered_count == 1 else "gaps"
        return f"{uncovered_count} uncovered {noun} need attention."
    if partial_count > 0:
        noun = "gap" if partial_count == 1 else "gaps"
        return f"{partial_count} partially covered {noun} are worth reviewing."
    noun = "gap" if total_active == 1 else "gaps"
    return f"{total_active} active {noun} are being tracked."


def _cluster_activity_at(cluster: GapCluster) -> datetime:
    for value in (cluster.last_computed_at, cluster.last_question_at, cluster.created_at):
        if value is None:
            continue
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return datetime.min.replace(tzinfo=UTC)


def _effective_mode_b_status(cluster: GapCluster) -> GapClusterStatus:
    status = cluster.status if isinstance(cluster.status, GapClusterStatus) else GapClusterStatus(str(cluster.status))
    if status == GapClusterStatus.inactive:
        return GapClusterStatus.inactive
    if status not in {GapClusterStatus.closed, GapClusterStatus.dismissed}:
        return status
    reference_time = _cluster_activity_at(cluster)
    if reference_time <= datetime.now(UTC) - timedelta(days=GapLifecyclePolicy().inactive_days):
        return GapClusterStatus.inactive
    return status


def _mode_b_status_matches_filter(status_filter: ModeBStatusFilter, status: GapClusterStatus) -> bool:
    if status_filter == "active":
        return status == GapClusterStatus.active
    if status_filter == "archived":
        return status in {GapClusterStatus.closed, GapClusterStatus.dismissed, GapClusterStatus.inactive}
    if status_filter == "closed":
        return status == GapClusterStatus.closed
    if status_filter == "dismissed":
        return status == GapClusterStatus.dismissed
    if status_filter == "inactive":
        return status == GapClusterStatus.inactive
    return status in {
        GapClusterStatus.active,
        GapClusterStatus.closed,
        GapClusterStatus.dismissed,
        GapClusterStatus.inactive,
    }


def _mode_b_status_from_coverage(coverage_score: float) -> GapClusterStatus:
    if coverage_score >= CoveragePolicy().covered_threshold:
        return GapClusterStatus.closed
    return GapClusterStatus.active
