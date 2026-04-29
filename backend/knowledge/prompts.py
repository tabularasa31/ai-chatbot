"""HippoRAG-style prompts for entity and triple extraction.

Adapted from the HippoRAG project (https://github.com/OSU-NLP-Group/HippoRAG,
MIT License, OSU NLP Group). The structure (system + one-shot + final user
message, JSON output) follows their templates; one-shot examples and system
text are rewritten for chat9's multilingual FAQ pipeline.

Three prompts:

- ``build_ner_query_messages``: extract entities from a user question. Used at
  query time to add an entity-overlap channel to hybrid retrieval (alongside
  dense + BM25 in ``backend/search/service.py``).
- ``build_ner_passage_messages``: extract entities from an FAQ chunk. Used at
  indexing time to populate the entity index for the overlap channel.
- ``build_triple_extraction_messages``: extract (subject, relation, object)
  triples conditioned on a passage's named entities. Reserved for future
  knowledge-graph experiments; not wired into retrieval yet.

Conventions:

- The whole prompt — system text and one-shot — is in English. One-shot
  examples use clearly fictional placeholder products (Acme / Foo / Bar /
  Baz) so the model has no real-brand baggage.
- The system prompt explicitly requires preserving the source-language
  surface form, so the prompt stays language-agnostic at inference: a
  Russian or Spanish input produces same-language entities verbatim.
- Output is strict JSON. Callers should request ``response_format={"type":
  "json_object"}`` and parse with ``json.loads``.
"""

from __future__ import annotations

_NER_PASSAGE_SYSTEM = (
    "You extract named entities from a passage of text.\n"
    "Return a JSON object with key 'named_entities' whose value is a list of strings.\n"
    "Preserve each entity exactly as it appears in the source — same language, "
    "same surface form. Do not translate or normalize."
)

_NER_PASSAGE_ONE_SHOT_INPUT = (
    "The Pro plan in Acme CRM costs $59 per month and includes integration "
    "with FooChat, BarMail, and BazDrive. Launched on March 1, 2024."
)

_NER_PASSAGE_ONE_SHOT_OUTPUT = (
    '{"named_entities": ["Pro plan", "Acme CRM", "$59 per month", "FooChat", '
    '"BarMail", "BazDrive", "March 1, 2024"]}'
)


def build_ner_passage_messages(passage: str) -> list[dict[str, str]]:
    """Chat messages for extracting named entities from an FAQ passage."""
    return [
        {"role": "system", "content": _NER_PASSAGE_SYSTEM},
        {"role": "user", "content": _NER_PASSAGE_ONE_SHOT_INPUT},
        {"role": "assistant", "content": _NER_PASSAGE_ONE_SHOT_OUTPUT},
        {"role": "user", "content": passage},
    ]


_NER_QUERY_SYSTEM = (
    "You extract named entities from a user question.\n"
    "Return a JSON object with key 'named_entities' whose value is a list of strings.\n"
    "Include product names, brands, features, plans, dates, and other specific "
    "terms a retrieval system would use as keywords. Skip generic words.\n"
    "Preserve each entity exactly as it appears in the source — same language, "
    "same surface form. Do not translate or normalize."
)

_NER_QUERY_ONE_SHOT_INPUT = (
    "How much does the Pro plan in Acme CRM cost and does it integrate with FooChat?"
)

_NER_QUERY_ONE_SHOT_OUTPUT = (
    '{"named_entities": ["Pro plan", "Acme CRM", "FooChat"]}'
)


def build_ner_query_messages(query: str) -> list[dict[str, str]]:
    """Chat messages for extracting named entities from a user query."""
    return [
        {"role": "system", "content": _NER_QUERY_SYSTEM},
        {"role": "user", "content": _NER_QUERY_ONE_SHOT_INPUT},
        {"role": "assistant", "content": _NER_QUERY_ONE_SHOT_OUTPUT},
        {"role": "user", "content": query},
    ]


_TRIPLE_EXTRACTION_SYSTEM = (
    "You construct an RDF-style knowledge graph from a passage and its list "
    "of named entities.\n"
    "Return a JSON object with key 'triples' whose value is a list of "
    "[subject, relation, object] arrays.\n"
    "Requirements:\n"
    "- Each triple should contain at least one — preferably two — of the "
    "provided named entities.\n"
    "- Resolve pronouns to their explicit names.\n"
    "- Preserve subject, relation, and object exactly in the source language "
    "and surface form. Do not translate or normalize."
)

_TRIPLE_USER_TEMPLATE = (
    "Extract triples from the passage into a JSON object with the single "
    "key 'triples', conditioned on the provided named entities.\n"
    "Passage:\n"
    "```\n"
    "{passage}\n"
    "```\n"
    "Named entities:\n"
    "{named_entities}"
)

_TRIPLE_ONE_SHOT_INPUT = _TRIPLE_USER_TEMPLATE.format(
    passage=_NER_PASSAGE_ONE_SHOT_INPUT,
    named_entities=_NER_PASSAGE_ONE_SHOT_OUTPUT,
)

_TRIPLE_ONE_SHOT_OUTPUT = (
    '{"triples": ['
    '["Pro plan", "is offered by", "Acme CRM"], '
    '["Pro plan", "costs", "$59 per month"], '
    '["Pro plan", "integrates with", "FooChat"], '
    '["Pro plan", "integrates with", "BarMail"], '
    '["Pro plan", "integrates with", "BazDrive"], '
    '["Pro plan", "launched on", "March 1, 2024"]'
    "]}"
)


def build_triple_extraction_messages(
    passage: str, named_entities_json: str
) -> list[dict[str, str]]:
    """Chat messages for NER-conditioned triple extraction.

    ``named_entities_json`` is the raw JSON string returned by the NER step
    on the same passage (the assistant's response from
    ``build_ner_passage_messages``).
    """
    user_content = _TRIPLE_USER_TEMPLATE.format(
        passage=passage, named_entities=named_entities_json
    )
    return [
        {"role": "system", "content": _TRIPLE_EXTRACTION_SYSTEM},
        {"role": "user", "content": _TRIPLE_ONE_SHOT_INPUT},
        {"role": "assistant", "content": _TRIPLE_ONE_SHOT_OUTPUT},
        {"role": "user", "content": user_content},
    ]
