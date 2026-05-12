"""Shared enum definitions for Gap Analyzer."""

from __future__ import annotations

from enum import Enum


class GapSource(str, Enum):
    mode_a = "mode_a"
    mode_b = "mode_b"


class GapRunMode(str, Enum):
    mode_a = "mode_a"
    mode_b = "mode_b"
    both = "both"


class GapCommandStatus(str, Enum):
    accepted = "accepted"
    in_progress = "in_progress"
    rate_limited = "rate_limited"


class GapJobKind(str, Enum):
    mode_a = "mode_a"
    mode_b = "mode_b"
    mode_b_weekly_reclustering = "mode_b_weekly_reclustering"


class GapJobStatus(str, Enum):
    queued = "queued"
    in_progress = "in_progress"
    retry = "retry"
    completed = "completed"
    failed = "failed"


class GapClusterStatus(str, Enum):
    active = "active"
    dismissed = "dismissed"
    closed = "closed"
    inactive = "inactive"
    drafting = "drafting"
    in_review = "in_review"
    resolved = "resolved"


class GapDocTopicStatus(str, Enum):
    active = "active"
    closed = "closed"


class GapDismissReason(str, Enum):
    feature_request = "feature_request"
    not_relevant = "not_relevant"
    already_covered = "already_covered"
    other = "other"
