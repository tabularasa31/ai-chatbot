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


class GapClusterStatus(str, Enum):
    active = "active"
    dismissed = "dismissed"
    closed = "closed"
    inactive = "inactive"


class GapDocTopicStatus(str, Enum):
    active = "active"
    closed = "closed"


class GapDismissReason(str, Enum):
    feature_request = "feature_request"
    not_relevant = "not_relevant"
    already_covered = "already_covered"
    other = "other"
