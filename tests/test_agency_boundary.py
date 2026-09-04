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


def test_prompt_forbids_offering_an_action_or_collecting_its_values() -> None:
    boundary = _boundary(build_rag_prompt("How do I change the IP?", ["chunk"]))

    assert "Never offer to perform such an action" in boundary
    assert "never ask the user for the value you would need in order to perform it" in boundary


def test_documented_steps_stay_in_the_users_hands() -> None:
    boundary = _boundary(build_rag_prompt("How do I change the IP?", ["chunk"]))

    assert "steps the user carries out in their own account or panel" in boundary
    assert "never a description of something you do" in boundary


def test_first_report_of_a_failing_step_asks_before_it_offers_the_handoff() -> None:
    boundary = _boundary(build_rag_prompt("The IP will not save", ["chunk"]))

    assert "the handoff is not the next move either" in boundary
    assert "must never arrive before the troubleshooting" in boundary
    assert "at most one short question stands between the report and the handoff" in boundary.lower()


def test_the_boundary_uses_the_marker_literals_the_backend_detects() -> None:
    """Renaming a marker constant must not silently desync the prompt from the parser."""
    boundary = _boundary(build_rag_prompt("The IP will not save", ["chunk"]))

    assert f"`{CLARIFY_MARKER}`" in boundary
    assert f"`{HANDOFF_MARKER}`" in boundary


def test_the_troubleshooting_question_yields_to_the_per_turn_clarification_ban() -> None:
    """A turn out of clarification budget is told not to ask; the block must not
    demand a question anyway, or that turn ends with neither question nor handoff."""
    boundary = _boundary(
        build_rag_prompt("The IP will not save", ["chunk"], allow_clarification=False)
    )

    assert "Skip this check entirely when the turn's clarification instruction forbids asking" in boundary
    assert "go straight to the handoff marker below" in boundary


def test_one_question_covers_both_the_walked_path_and_the_ticket_substance() -> None:
    """The pre-existing substance check already spends the single question, so the
    two must be the same question — else a ticket goes out with no error text."""
    boundary = _boundary(build_rag_prompt("The IP will not save", ["chunk"]))

    assert "one question that does both jobs at once" in boundary
    assert "satisfies the substance check above rather than adding a second round" in boundary


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
