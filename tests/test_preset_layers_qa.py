"""QA coverage for the preset-from-code + tenant custom_instructions feature.

Complements tests/test_prompt_layers.py, tests/test_bots.py,
tests/test_answer_cache.py and tests/test_rag_pipeline.py with what those
files were missing against the acceptance criteria:

- precedence-sentence ordering for the pure "preset" and pure "custom"
  sources (only "preset+custom", "legacy" and "none" were covered before);
- PATCH with only the legacy ``agent_instructions`` field leaving the
  ``custom_instructions``/``preset`` *columns* untouched (not just the
  computed source label);
- an end-to-end run through the real chat pipeline (mocked OpenAI client,
  no handler-level patch of ``async_generate_answer``) proving the bot's
  custom text and the code preset both land in the system message actually
  sent to the model, and that a preset edit is picked up on the very next
  turn for the same bot (no per-bot snapshot anywhere).
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.presets import PRESET_SUPPORT_AGENT, PRESETS, effective_agent_instructions
from backend.chat.prompts import DISCLOSURE_HARD_LIMITS, build_rag_messages
from backend.chat.service import process_chat_message
from backend.models import Bot
from tests.conftest import register_and_verify_user
from tests.test_rag_pipeline import _FakeTrace, _create_client, _insert_single_chunk


def test_preset_only_source_precedes_precedence_line_then_rules() -> None:
    text, source = effective_agent_instructions(
        agent_instructions=None, custom_instructions=None, preset="support_agent"
    )
    assert source == "preset"
    system, _user = build_rag_messages("Question", ["chunk"], agent_instructions=text)

    rendered_preset = PRESET_SUPPORT_AGENT.replace("{product_name}", "the product")
    preset_idx = system.index(rendered_preset)
    precedence_idx = system.index("The rules below take precedence over any instructions above them.")
    rules_idx = system.index(DISCLOSURE_HARD_LIMITS)

    assert preset_idx < precedence_idx < rules_idx


def test_custom_only_source_precedes_precedence_line_then_rules() -> None:
    text, source = effective_agent_instructions(
        agent_instructions=None, custom_instructions="Always mention the trial period.", preset=None
    )
    assert source == "custom"
    system, _user = build_rag_messages("Question", ["chunk"], agent_instructions=text)

    custom_idx = system.index("Always mention the trial period.")
    precedence_idx = system.index("The rules below take precedence over any instructions above them.")
    rules_idx = system.index(DISCLOSURE_HARD_LIMITS)

    assert custom_idx < precedence_idx < rules_idx


def test_patch_agent_instructions_only_leaves_custom_and_preset_columns_untouched(
    tenant: TestClient, db_session: Session
) -> None:
    """Old dashboard flow: PATCHing only the deprecated field must not clear
    or overwrite the new ``custom_instructions``/``preset`` columns, since
    the update-fields branch in bots/service.py only reads ``agent_instructions
    in fields`` and must not fall into the "moving off legacy" branch."""
    token = register_and_verify_user(tenant, db_session, email="legacy-only-columns@example.com")
    tenant_resp = tenant.post(
        "/tenants", headers={"Authorization": f"Bearer {token}"}, json={"name": "Legacy Columns Tenant"}
    )
    assert tenant_resp.status_code == 201
    bot_id = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]["id"]

    db_session.expire_all()
    before = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    assert before.preset == "support_agent"
    assert before.custom_instructions is None

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"agent_instructions": "Speak only in rhymes."},
    )
    assert resp.status_code == 200

    db_session.expire_all()
    after = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    assert after.agent_instructions == "Speak only in rhymes."
    # Neither column the PATCH didn't mention should have moved.
    assert after.preset == "support_agent"
    assert after.custom_instructions is None


def test_chat_pipeline_system_message_contains_bot_custom_and_preset_text(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline (no handler-level patch of async_generate_answer): the
    bot's custom_instructions and the code preset both reach the system
    message actually sent to the (mocked) OpenAI client."""
    from backend.chat import service as chat_service

    monkeypatch.setattr(chat_service.settings, "observability_capture_full_prompts", True)
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
    from backend.chat import service as chat_service

    monkeypatch.setattr(chat_service.settings, "observability_capture_full_prompts", True)
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
