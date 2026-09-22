"""QA coverage for the preset-from-code + tenant custom_instructions feature.

Complements tests/test_prompt_layers.py, tests/test_bots.py,
tests/test_answer_cache.py and tests/test_rag_pipeline.py with what those
files were missing against the acceptance criteria: an end-to-end run
through the real chat pipeline (mocked OpenAI client, no handler-level patch
of ``async_generate_answer``) proving the bot's custom text and the code
preset both land in the system message actually sent to the model, and that
a preset edit is picked up on the very next turn for the same bot (no
per-bot snapshot anywhere). Precedence-sentence ordering for every source
combination (preset-only, custom-only, preset+custom, none) is covered in
tests/test_prompt_layers.py.
"""

from __future__ import annotations

from backend.core.config import settings

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.presets import PRESET_SUPPORT_AGENT, PRESETS
from backend.chat.service import (
    process_chat_message,
)
from backend.models import Bot
from tests.test_rag_pipeline import _FakeTrace, _create_client, _insert_single_chunk


def test_chat_pipeline_system_message_contains_bot_custom_and_preset_text(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline (no handler-level patch of async_generate_answer): the
    bot's custom_instructions and the code preset both reach the system
    message actually sent to the (mocked) OpenAI client."""

    monkeypatch.setattr(settings, "observability_capture_full_prompts", True)
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    cl_row, api_key = _create_client(tenant, db_session, email="preset-e2e@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id, chunk_text="Some docs chunk.")

    bot = db_session.query(Bot).filter(Bot.tenant_id == cl_row.id).first()
    bot.custom_instructions = "Always mention our 14-day refund window."
    db_session.commit()

    process_chat_message(
        cl_row.id,
        "How do I reset my password?",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
        bot_id=bot.id,
    )

    assert fake_trace.generation_calls, "generation should have happened"
    system_prompt = fake_trace.generation_calls[-1]["input"][0]["content"]
    preset_opening = PRESET_SUPPORT_AGENT.split("\n")[0].split("{product_name}")[0]
    assert "Always mention our 14-day refund window." in system_prompt
    assert preset_opening in system_prompt


def test_chat_pipeline_picks_up_preset_change_on_next_turn_no_snapshot(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editing the code preset must change what the *same* bot sends on its
    very next turn — proving there is no per-bot snapshot of the preset text
    stored anywhere in the pipeline."""

    monkeypatch.setattr(settings, "observability_capture_full_prompts", True)
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    cl_row, api_key = _create_client(tenant, db_session, email="preset-e2e-swap@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id, chunk_text="Some docs chunk.")
    bot = db_session.query(Bot).filter(Bot.tenant_id == cl_row.id).first()
    assert bot.preset == "support_agent"

    process_chat_message(
        cl_row.id, "First question", uuid.uuid4(), db_session, api_key=api_key, bot_id=bot.id
    )
    first_system_prompt = fake_trace.generation_calls[-1]["input"][0]["content"]
    assert "REPLACED PRESET TEXT" not in first_system_prompt

    monkeypatch.setitem(PRESETS, "support_agent", "REPLACED PRESET TEXT for {product_name}.")

    process_chat_message(
        cl_row.id, "Second question", uuid.uuid4(), db_session, api_key=api_key, bot_id=bot.id
    )
    second_system_prompt = fake_trace.generation_calls[-1]["input"][0]["content"]
    assert "REPLACED PRESET TEXT" in second_system_prompt
