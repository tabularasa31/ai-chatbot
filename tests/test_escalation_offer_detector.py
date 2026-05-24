"""Unit tests for backend.escalation.offer_detector."""

from backend.escalation.offer_detector import looks_like_escalation_offer


def test_russian_open_ticket_offer():
    assert looks_like_escalation_offer(
        "В документации этого нет. "
        "Хотите, чтобы я открыл тикет в поддержку, и команда подтвердила это вам по электронной почте?"
    )


def test_russian_forward_to_support():
    assert looks_like_escalation_offer(
        "Передать ваш вопрос в команду поддержки?"
    )


def test_english_open_support_ticket_offer():
    assert looks_like_escalation_offer(
        "I couldn't find that. Want me to open a support ticket so the team can email you back?"
    )


def test_english_would_you_like_support():
    assert looks_like_escalation_offer(
        "Would you like me to forward this to the support team?"
    )


def test_plain_answer_does_not_trigger():
    assert not looks_like_escalation_offer(
        "DNS records can be edited in the TurboFlare panel. Each change takes effect within a minute."
    )


def test_incidental_ticket_mention_does_not_trigger():
    # "ticket" appears but without an action verb in front
    assert not looks_like_escalation_offer(
        "Your ticket ID is shown at the top of the response page."
    )


def test_empty_input():
    assert not looks_like_escalation_offer("")
    assert not looks_like_escalation_offer(None)  # type: ignore[arg-type]
