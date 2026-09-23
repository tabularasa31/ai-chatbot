"""The bot must never offer to act inside the tenant's product.

Origin (escalation ESC-0608, tenant panel product): the user reported that the IP
would not save, and the bot answered "I can change the IP in the panel — tell me
the new IP and whether HTTPS should be enabled". It has no panel access at all.
The documentation those pages came from is written for the tenant's own customers
— steps they carry out in their panel — and nothing in the system prompt drew the
line between handing someone steps and performing them, so the model read the
instructions as its own capabilities and started collecting values for a change
that would never happen.

The same turn showed the other half: the first report that a documented step fails
went straight to a handoff offer. Saying "yes" to one costs the user nothing, so
the offer has to come after the steps have actually been walked, not instead.
"""

from __future__ import annotations

import pytest

from backend.chat.prompts import CAPABILITY_BOUNDARY, build_rag_messages, build_rag_prompt
from backend.chat.streaming import CLARIFY_MARKER, HANDOFF_MARKER


def _boundary(prompt: str) -> str:
    """The boundary block as it reaches the model, not as it is written."""
    return prompt.split("WHAT YOU CAN DO:", 1)[1]


def test_prompt_states_the_bot_cannot_act_in_the_product() -> None:
    prompt = build_rag_prompt("How do I change the IP?", ["some documentation chunk"])

    assert "You have no access to the tenant's panel" in prompt
    assert "you cannot change a setting" in prompt


def test_boundary_block_encodes_the_full_agency_and_handoff_contract() -> None:
    """One boundary block, every rule it must encode for a failing documented step.

    Guards: forbids offering an action or collecting the values it would need;
    documented steps stay in the user's hands, never described as something the
    bot does; a first failure report asks before it offers the handoff; the
    marker literals it names must be the ones the backend actually parses; and
    the substance check and the troubleshooting question must be the same
    single question, not two rounds.
    """
    boundary = _boundary(build_rag_prompt("The IP will not save", ["chunk"]))

    assert "Never offer to perform such an action" in boundary
    assert "never ask the user for the value you would need in order to perform it" in boundary
    assert "steps the user carries out in their own account or panel" in boundary
    assert "never a description of something you do" in boundary
    assert "the handoff is not the next move either" in boundary
    assert "must never arrive before the troubleshooting" in boundary
    assert "at most one short question stands between the report and the handoff" in boundary.lower()
    assert f"`{CLARIFY_MARKER}`" in boundary
    assert f"`{HANDOFF_MARKER}`" in boundary
    assert "one question that does both jobs at once" in boundary
    assert "satisfies the substance check above rather than adding a second round" in boundary


def test_the_troubleshooting_question_yields_to_the_per_turn_clarification_ban() -> None:
    """A turn out of clarification budget is told not to ask; the block must not
    demand a question anyway, or that turn ends with neither question nor handoff."""
    boundary = _boundary(
        build_rag_prompt("The IP will not save", ["chunk"], allow_clarification=False)
    )

    assert "Skip this check entirely when the turn's clarification instruction forbids asking" in boundary
    assert "go straight to the handoff marker below" in boundary


@pytest.mark.parametrize("allow_clarification", [True, False])
def test_boundary_lives_in_the_stable_system_prefix(allow_clarification: bool) -> None:
    """Prompt-cache contract: the block is bot-stable, so it must sit in the system
    message and stay byte-identical no matter what the request looks like."""
    system, user = build_rag_messages(
        "The IP will not save",
        ["chunk"],
        response_language="ru",
        allow_clarification=allow_clarification,
    )
    baseline, _ = build_rag_messages("Where do I find pricing?", ["another chunk"])

    assert CAPABILITY_BOUNDARY in system
    assert CAPABILITY_BOUNDARY not in user
    assert system == baseline
