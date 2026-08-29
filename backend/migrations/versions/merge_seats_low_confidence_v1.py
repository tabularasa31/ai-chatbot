"""Rejoin the two heads that a pair of parallel merges left behind.

``operator_seats_v1`` (per-person seats) and ``low_confidence_second_attempt_v1``
(handoff on the second weak turn) were written on branches cut from the same
parent and merged within an hour of each other. Neither is wrong and neither
touches what the other touches; they simply both claim
``member_removal_signatures_v1`` as their parent, which leaves the graph with
two heads.

That is not a latent problem. ``alembic upgrade head`` refuses to choose
between heads, so the Railway release step failed on every attempt and the API
stayed down from 18:14 UTC until this landed:

    FAILED: Multiple head revisions are present for given argument 'head'

This revision does nothing but name both as parents, which is the whole of the
repair — there is no schema change here and there is nothing to undo.

The lesson is cheaper than the outage: a branch carrying a migration has to
re-check ``alembic heads`` against fresh ``main`` immediately before merge, not
when the branch was opened. Both of these were single-headed when written.

Revision ID: merge_seats_low_confidence_v1
Revises: low_confidence_second_attempt_v1, operator_seats_v1
"""

from __future__ import annotations

# 29 characters. ``alembic_version.version_num`` is VARCHAR(32), and the
# first name for this revision was 33 — the migration itself ran and then
# the write of its own version number failed, which took production down a
# second time. Keep any new id comfortably under the limit.
revision = "merge_seats_low_confidence_v1"
down_revision = ("low_confidence_second_attempt_v1", "operator_seats_v1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nothing to do — this revision exists only to rejoin the graph."""


def downgrade() -> None:
    """Nothing to undo, and downgrades are never run here (see CLAUDE.md)."""
