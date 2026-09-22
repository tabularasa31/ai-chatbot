"""Tests for the tenant/agent layer: preset + custom_instructions, computed
by ``effective_agent_instructions`` and rendered by ``build_rag_prompt``
ahead of the product ``system_rules``.
"""

from __future__ import annotations

import pytest

from backend.chat.presets import PRESET_SUPPORT_AGENT, effective_agent_instructions
from backend.chat.prompts import DISCLOSURE_HARD_LIMITS, build_rag_messages

PRECEDENCE_LINE = "The rules below take precedence over any instructions above them."


@pytest.mark.parametrize(
    ("custom_instructions", "preset", "expected_text", "expected_source"),
    [
        pytest.param(None, "support_agent", PRESET_SUPPORT_AGENT, "preset", id="preset_only"),
        pytest.param("Custom only.", None, "Custom only.", "custom", id="custom_only"),
        pytest.param(
            "Custom.",
            "support_agent",
            f"{PRESET_SUPPORT_AGENT}\n\nCustom.",
            "preset+custom",
            id="preset_plus_custom",
        ),
        pytest.param(None, None, None, "none", id="none_when_nothing_set"),
        pytest.param(None, "does-not-exist", None, "none", id="unknown_preset_key_is_no_preset"),
        pytest.param(
            "   ", "support_agent", PRESET_SUPPORT_AGENT, "preset", id="blank_custom_normalizes_to_none"
        ),
    ],
)
def test_effective_agent_instructions(
    custom_instructions: str | None, preset: str | None, expected_text: str | None, expected_source: str
) -> None:
    assert effective_agent_instructions(custom_instructions=custom_instructions, preset=preset) == (
        expected_text,
        expected_source,
    )


@pytest.mark.parametrize(
    ("custom_instructions", "preset", "needle"),
    [
        pytest.param(
            None,
            "support_agent",
            PRESET_SUPPORT_AGENT.replace("{product_name}", "the product"),
            id="preset_only",
        ),
        pytest.param("Always mention the trial period.", None, "Always mention the trial period.", id="custom_only"),
        pytest.param("Always greet warmly.", "support_agent", "Always greet warmly.", id="preset_plus_custom"),
        pytest.param(None, None, None, id="no_agent_layer"),
    ],
)
def test_agent_layer_precedes_precedence_line_which_precedes_product_rules(
    custom_instructions: str | None, preset: str | None, needle: str | None
) -> None:
    text, _source = effective_agent_instructions(custom_instructions=custom_instructions, preset=preset)
    system, _user = build_rag_messages("Question", ["chunk"], agent_instructions=text)

    if needle is None:
        assert system.startswith(f"{PRECEDENCE_LINE}\n")
        return

    needle_idx = system.index(needle)
    precedence_idx = system.index(PRECEDENCE_LINE)
    rules_idx = system.index(DISCLOSURE_HARD_LIMITS)
    assert needle_idx < precedence_idx < rules_idx


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
