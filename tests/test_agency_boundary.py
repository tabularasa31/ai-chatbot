"""The bot must never offer to act inside the tenant's product.

Origin (escalation ESC-0608, tenant panel product): the user reported that the IP
would not save, and the bot answered "I can change the IP in the panel — tell me
the new IP and whether HTTPS should be enabled". It has no panel access at all.
The documentation those pages came from is written for the tenant's own customers
— steps they carry out in their panel — and nothing in the system prompt drew the
line between handing someone steps and performing them, so the model read the
instructions as its own capabilities and started collecting values for a change
that would never happen.

Covered here: the prompt states the boundary, forbids collecting the values such
an action would need, keeps documented steps in the user's hands, and puts one
troubleshooting question — not a handoff offer — in front of the first report
that a documented step is failing. Saying "yes" to a forwarding offer costs the
user nothing, so the offer must come after the steps have actually been walked.
"""

from __future__ import annotations

from backend.chat.prompts import build_rag_prompt


def test_prompt_states_the_bot_cannot_act_in_the_product() -> None:
    prompt = build_rag_prompt("Как поменять IP?", ["some documentation chunk"])

    assert "You have no access to the tenant's panel" in prompt
    assert "you cannot change a setting" in prompt


def test_prompt_forbids_offering_an_action_or_collecting_its_values() -> None:
    prompt = build_rag_prompt("Как поменять IP?", ["some documentation chunk"])

    assert "Never offer to perform such an action" in prompt
    assert "never ask the user for the value you would need in order to perform it" in prompt


def test_documented_steps_stay_in_the_users_hands() -> None:
    prompt = build_rag_prompt("Как поменять IP?", ["some documentation chunk"])

    assert "steps the user carries out in their own account or panel" in prompt
    assert "never a description of something you do" in prompt


def test_first_report_of_a_failing_step_asks_before_it_offers_the_handoff() -> None:
    boundary = build_rag_prompt("Не сохраняется IP", ["chunk"]).split("WHAT YOU CAN DO:", 1)[1]

    assert "the handoff is not the next move either" in boundary
    assert "Make sure they have actually walked the documented path first" in boundary
    assert "mark that reply `<clarifying/>`" in boundary
    assert "must never arrive before the troubleshooting" in boundary


def test_handoff_follows_the_confirmed_steps_and_stays_a_single_question() -> None:
    boundary = build_rag_prompt("Не сохраняется IP", ["chunk"]).split("WHAT YOU CAN DO:", 1)[1]

    assert "Append `<needs_human/>` once the user has confirmed they followed the steps" in boundary
    assert "at most one short question stands between the report and the handoff" in boundary
    assert "never re-ask a step the user already said they took" in boundary


def test_boundary_is_part_of_the_stable_system_prefix() -> None:
    system_part = build_rag_prompt("q", ["chunk"]).split("\n\nContext:\n", 1)[0]

    assert "WHAT YOU CAN DO:" in system_part
