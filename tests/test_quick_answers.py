from __future__ import annotations

import inspect
import uuid

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session, sessionmaker

from backend.chat.service import _quick_answer_quality_score
from backend.documents.quick_answers import (
    _extract_documentation_url,
    _extract_support_email,
    _extract_trial_info,
)
from backend.models import Tenant, QuickAnswer, SourceSchedule, SourceStatus, UrlSource, User
from scripts.cleanup_quick_answers import run_cleanup


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _create_quick_answer(
    db_session: Session,
    *,
    key: str,
    value: str,
    metadata_json: dict[str, str],
) -> QuickAnswer:
    suffix = uuid.uuid4().hex
    user = User(
        email=f"user-{suffix}@example.com",
        password_hash="hashed",
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()

    tenant = Tenant(
                name=f"Tenant {suffix}",)
    db_session.add(tenant)
    db_session.flush()

    source = UrlSource(
        tenant_id=tenant.id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()

    answer = QuickAnswer(
        tenant_id=tenant.id,
        source_id=source.id,
        key=key,
        value=value,
        source_url="https://docs.example.com/contact",
        metadata_json=metadata_json,
    )
    db_session.add(answer)
    db_session.commit()
    db_session.refresh(answer)
    return answer


def test_mailto_support_email_accepted() -> None:
    html = '<a href="mailto:support@acme.com">Contact support</a>'

    candidate = _extract_support_email(_soup(html), "Contact support", "https://acme.com/contact")

    assert candidate is not None
    assert candidate.value == "support@acme.com"
    assert candidate.score >= 135


def test_mailto_blocklisted_email_rejected() -> None:
    blocked = "privacy" + "@acme.com"
    html = f'<a href="mailto:{blocked}">Privacy</a>'

    candidate = _extract_support_email(_soup(html), blocked, "https://acme.com/contact")

    assert candidate is None


def test_mailto_no_reply_rejected() -> None:
    html = '<a href="mailto:no-reply@acme.com">Do not reply</a>'

    candidate = _extract_support_email(_soup(html), "Do not reply", "https://acme.com/contact")

    assert candidate is None


def test_email_too_long_rejected() -> None:
    local = "a" * 50
    html = f'<a href="mailto:{local}@acme.com">Email us</a>'

    candidate = _extract_support_email(_soup(html), html, "https://acme.com/contact")

    assert candidate is None


def test_email_malformed_double_dot_rejected() -> None:
    html = '<a href="mailto:a..b@x.com">Email us</a>'

    candidate = _extract_support_email(_soup(html), html, "https://acme.com/contact")

    assert candidate is None


def test_regex_fallback_skips_blocklisted() -> None:
    blocked = "legal" + "@acme.com"

    candidate = _extract_support_email(_soup(f"<p>{blocked}</p>"), blocked, "https://acme.com/legal")

    assert candidate is None


def test_regex_fallback_accepts_plain_support_email() -> None:
    text = "Write us at hello@acme.com"

    candidate = _extract_support_email(_soup(f"<p>{text}</p>"), text, "https://acme.com/contact")

    assert candidate is not None
    assert candidate.value == "hello@acme.com"
    assert candidate.score == 70


def test_mailto_multiple_picks_best_score() -> None:
    html = """
    <a href="mailto:info@acme.com">General contact</a>
    <a href="mailto:support@acme.com">Contact support</a>
    """

    candidate = _extract_support_email(_soup(html), "Contact support", "https://acme.com/contact")

    assert candidate is not None
    assert candidate.value == "support@acme.com"


def test_trial_full_sentence() -> None:
    candidate = _extract_trial_info(
        "Try our 14-day free trial. No credit card required.",
        "https://acme.com/pricing",
    )

    assert candidate is not None
    assert candidate.value == "Try our 14-day free trial."


def test_trial_no_sentence_punct_truncates_at_240() -> None:
    text = (
        "Learn more about our product and start your free trial today with onboarding guidance "
        "for every team size and extra supporting words repeated to make the sentence very long "
        "without punctuation so it needs to be trimmed carefully at a sensible word boundary for users"
    )

    candidate = _extract_trial_info(text, "https://acme.com/pricing")

    assert candidate is not None
    assert candidate.value.endswith("…")
    assert len(candidate.value) <= 240
    assert not candidate.value.endswith(" …")


def test_trial_rejects_999_days() -> None:
    candidate = _extract_trial_info("Get 999-day trial now", "https://acme.com/pricing")

    assert candidate is None


def test_trial_accepts_30_day() -> None:
    candidate = _extract_trial_info("30-day free trial available", "https://acme.com/pricing")

    assert candidate is not None
    assert candidate.value == "30-day free trial available"


def test_trial_picks_first_matching_sentence() -> None:
    text = "Welcome aboard. Start your free trial today. Later we mention another free trial."

    candidate = _extract_trial_info(text, "https://acme.com/pricing")

    assert candidate is not None
    assert candidate.value == "Start your free trial today."


def test_trial_returns_none_when_no_match() -> None:
    candidate = _extract_trial_info("Plans are billed annually.", "https://acme.com/pricing")

    assert candidate is None


def test_documentation_url_returns_none_without_anchors() -> None:
    candidate = _extract_documentation_url(
        _soup("<p>No docs link here</p>"),
        "https://acme.com/start",
        "https://acme.com/",
    )

    assert candidate is None


def test_documentation_url_prefers_same_host() -> None:
    html = """
    <a href="https://external.io/docs">Documentation</a>
    <a href="https://acme.com/docs">Docs</a>
    """

    candidate = _extract_documentation_url(
        _soup(html),
        "https://acme.com/start",
        "https://acme.com/",
    )

    assert candidate is not None
    assert candidate.value == "https://acme.com/docs"
    assert candidate.score == 90


def test_quick_answer_quality_score_drops_removed_method() -> None:
    removed_method = "root" "_fallback"
    source = inspect.getsource(_quick_answer_quality_score)

    assert removed_method not in source


def test_cleanup_script_dry_run_deletes_nothing(engine) -> None:
    testing_session_local = sessionmaker(bind=engine, class_=Session, future=True)
    blocked = "privacy" + "@x.com"
    with testing_session_local() as db_session:
        answer = _create_quick_answer(
            db_session,
            key="support_email",
            value=blocked,
            metadata_json={"method": "regex"},
        )
        answer_id = answer.id

    removed = run_cleanup(dry_run=True, session_factory=testing_session_local)

    with testing_session_local() as verify_session:
        assert removed == 1
        assert verify_session.get(QuickAnswer, answer_id) is not None


def test_cleanup_script_removes_blocklisted_email(engine) -> None:
    testing_session_local = sessionmaker(bind=engine, class_=Session, future=True)
    blocked = "privacy" + "@x.com"
    with testing_session_local() as db_session:
        answer = _create_quick_answer(
            db_session,
            key="support_email",
            value=blocked,
            metadata_json={"method": "regex"},
        )
        answer_id = answer.id

    removed = run_cleanup(dry_run=False, session_factory=testing_session_local)

    with testing_session_local() as verify_session:
        assert removed == 1
        assert verify_session.get(QuickAnswer, answer_id) is None


def test_cleanup_script_idempotent(engine) -> None:
    testing_session_local = sessionmaker(bind=engine, class_=Session, future=True)
    blocked = "privacy" + "@x.com"
    with testing_session_local() as db_session:
        _create_quick_answer(
            db_session,
            key="support_email",
            value=blocked,
            metadata_json={"method": "regex"},
        )

    first_removed = run_cleanup(dry_run=False, session_factory=testing_session_local)
    second_removed = run_cleanup(dry_run=False, session_factory=testing_session_local)

    assert first_removed == 1
    assert second_removed == 0
