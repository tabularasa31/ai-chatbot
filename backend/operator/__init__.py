"""Live operator handoff — a human answering the visitor directly.

The chat thread is the single ledger: whichever channel an operator writes
through, the answer lands as a ``MessageRole.operator`` row in the same
conversation. ``service.ingest_from_operator`` is the one door in, so the
phase-1 inbound e-mail webhook and the later Telegram / Slack bridges are
additional callers rather than parallel copies of this logic.
"""
