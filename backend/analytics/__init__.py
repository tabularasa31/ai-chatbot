"""Tenant-facing analytics — a rolling-window summary computed on the fly.

No table stores these numbers: every request re-aggregates ``chats``,
``messages`` and ``escalation_tickets`` for the requested window, so there is
nothing to keep in sync and no backfill for periods before this module
existed.
"""
