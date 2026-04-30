"""Automated eval pipeline for chat9.

CLI: ``python -m backend.evals run --dataset <name> --bot-id <public_id>``

The runner sends each golden case to the chat backend, scores it with
deterministic checks (must_contain, language match, source citations)
and an LLM-as-judge backed by Anthropic Claude (independent of the
OpenAI model that generates answers, so the judge does not grade its
own family). Results are written as JSON + Markdown.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
