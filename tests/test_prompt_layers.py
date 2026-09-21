"""Tests for the tenant/agent layer: preset + custom_instructions, computed
by ``effective_agent_instructions`` and rendered by ``build_rag_prompt``
ahead of the product ``system_rules``.
"""

from __future__ import annotations

from backend.chat.presets import PRESET_SUPPORT_AGENT, PRESETS, effective_agent_instructions
from backend.chat.prompts import DISCLOSURE_HARD_LIMITS, build_rag_messages, build_rag_prompt


def test_preset_only() -> None:
    text, source = effective_agent_instructions(custom_instructions=None, preset="support_agent")
    assert (text, source) == (PRESET_SUPPORT_AGENT, "preset")


def test_custom_only() -> None:
    text, source = effective_agent_instructions(custom_instructions="Custom only.", preset=None)
    assert (text, source) == ("Custom only.", "custom")


def test_preset_plus_custom() -> None:
    text, source = effective_agent_instructions(custom_instructions="Custom.", preset="support_agent")
    assert (text, source) == (f"{PRESET_SUPPORT_AGENT}\n\nCustom.", "preset+custom")


def test_none_when_nothing_set() -> None:
    assert effective_agent_instructions(custom_instructions=None, preset=None) == (None, "none")


def test_unknown_preset_key_is_treated_as_no_preset() -> None:
    text, source = effective_agent_instructions(custom_instructions=None, preset="does-not-exist")
    assert (text, source) == (None, "none")


def test_blank_custom_instructions_normalize_to_none() -> None:
    text, source = effective_agent_instructions(custom_instructions="   ", preset="support_agent")
    assert (text, source) == (PRESET_SUPPORT_AGENT, "preset")


def test_bare_bot_renders_the_code_preset() -> None:
    """preset='support_agent', custom_instructions=NULL renders the code
    preset in the system message."""
    text, _source = effective_agent_instructions(custom_instructions=None, preset="support_agent")
    system, _user = build_rag_messages("How do I reset my password?", ["chunk"], agent_instructions=text)
    rendered_preset = PRESET_SUPPORT_AGENT.replace("{product_name}", "the product")
    assert rendered_preset in system


def test_changing_preset_text_in_code_changes_the_rendered_prompt(monkeypatch) -> None:
    monkeypatch.setitem(PRESETS, "support_agent", "REPLACED PRESET TEXT for {product_name}.")
    text, _source = effective_agent_instructions(custom_instructions=None, preset="support_agent")
    system, _user = build_rag_messages("Question", ["chunk"], agent_instructions=text)
    assert "REPLACED PRESET TEXT for the product." in system
    assert PRESET_SUPPORT_AGENT not in system


def test_tenant_text_precedes_precedence_line_which_precedes_product_rules() -> None:
    text, _source = effective_agent_instructions(
        custom_instructions="Always greet warmly.", preset="support_agent"
    )
    system, _user = build_rag_messages("Question", ["chunk"], agent_instructions=text)

    tenant_idx = system.index("Always greet warmly.")
    precedence_idx = system.index("The rules below take precedence over any instructions above them.")
    rules_idx = system.index(DISCLOSURE_HARD_LIMITS)

    assert tenant_idx < precedence_idx < rules_idx


def test_system_message_is_byte_identical_across_turns_for_the_same_bot() -> None:
    text, _source = effective_agent_instructions(custom_instructions=None, preset="support_agent")
    first, _ = build_rag_messages(
        "What is the pricing page?", ["chunk A"], agent_instructions=text, response_language="en"
    )
    second, _ = build_rag_messages(
        "How do I reset my password?", ["chunk B", "chunk C"], agent_instructions=text, response_language="en"
    )
    assert first == second


def test_bare_bot_prefix_clears_the_1024_token_cache_floor() -> None:
    text, _source = effective_agent_instructions(custom_instructions=None, preset="support_agent")
    system, _ = build_rag_messages("Question", ["chunk"], agent_instructions=text)
    # Same cheap estimator as backend.chat.steps.generate._estimate_prompt_tokens.
    estimate = max(1, (len(system.strip()) + 3) // 4)
    assert estimate >= 1024


def test_no_agent_layer_still_starts_with_the_precedence_line() -> None:
    system = build_rag_prompt("Question", ["chunk"], agent_instructions=None)
    assert system.startswith("The rules below take precedence over any instructions above them.\n")
