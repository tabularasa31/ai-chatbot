from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth.roles import ROLE_OPERATOR
from backend.auth.service import create_token_for_user
from backend.models import (
    Chat,
    Message,
    MessageRole,
    Tenant,
    TenantFaq,
    TenantProfile,
    UnansweredDismissReason,
    UnansweredGroup,
    UnansweredGroupStatus,
    UnansweredQuestion,
    UnansweredReason,
    User,
)
from backend.core.limiter import set_owner_jwt_rate_limit_key_override
from backend.models.base import _utcnow
from backend.unanswered.service import GENERATION_LOCK_TTL
from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client(http: TestClient, db: Session, *, email: str) -> tuple[str, Tenant]:
    token = register_and_verify_user(http, db, email=email)
    resp = http.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Knowledge API Tenant"},
    )
    assert resp.status_code in (200, 201), resp.text
    set_client_openai_key(http, token)
    client_row = db.get(Tenant, uuid.UUID(resp.json()["id"]))
    assert client_row is not None
    return token, client_row


def _kbase(_client_row: Tenant) -> str:
    return "/api/v1/knowledge"


@pytest.fixture
def faq_events(monkeypatch) -> list[dict]:
    """Every faq.reviewed event emitted during the test, in order."""
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.knowledge.events.capture_event", fake_capture)
    return events


def _update_profile(db: Session, tenant_id: uuid.UUID, **fields: object) -> TenantProfile:
    """Update the profile eager-created by create_tenant."""
    profile = db.get(TenantProfile, tenant_id)
    assert profile is not None, "create_tenant must eager-create the profile"
    for key, value in fields.items():
        setattr(profile, key, value)
    db.commit()
    return profile


def _make_faq(db: Session, tenant_id: uuid.UUID, **fields: object) -> TenantFaq:
    defaults = {"question": "Q", "answer": "A", "approved": False, "source": "docs"}
    defaults.update(fields)
    faq = TenantFaq(tenant_id=tenant_id, **defaults)
    db.add(faq)
    db.commit()
    db.refresh(faq)
    return faq


def test_get_profile_is_pure_read(tenant: TestClient, db_session: Session) -> None:
    """GET /knowledge/profile must never create DB rows (no lazy-create)."""
    token, client_row = _create_client(tenant, db_session, email="kapi-pure-read@example.com")

    # Profile is eager-created at tenant creation with pending defaults.
    profile = db_session.get(TenantProfile, client_row.id)
    assert profile is not None

    resp = tenant.get(f"{_kbase(client_row)}/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["extraction_status"] == "pending"

    # Without a profile row, GET returns 404 and does not create one.
    db_session.delete(profile)
    db_session.commit()

    resp = tenant.get(f"{_kbase(client_row)}/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404, resp.text
    db_session.expire_all()
    assert db_session.get(TenantProfile, client_row.id) is None


def test_profile_read_and_partial_update_journey(tenant: TestClient, db_session: Session) -> None:
    """Covers: GET reflects stored profile fields, PATCH updates only the given field."""
    token, client_row = _create_client(tenant, db_session, email="kapi-profile@example.com")
    _update_profile(
        db_session,
        client_row.id,
        product_name="Acme API",
        topics=["Payments"],
        support_email="help@acme.com",
        support_urls=["https://acme.com/docs"],
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )

    get_resp = tenant.get(f"{_kbase(client_row)}/profile", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 200, get_resp.text
    get_data = get_resp.json()
    assert get_data["product_name"] == "Acme API"
    assert get_data["extraction_status"] == "done"
    assert get_data["topics"] == ["Payments"]
    assert "modules" not in get_data

    patch_resp = tenant.patch(
        f"{_kbase(client_row)}/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"product_name": "Acme API v2"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patch_data = patch_resp.json()
    assert patch_data["product_name"] == "Acme API v2"
    assert patch_data["topics"] == ["Payments"]


def test_get_faq_filters(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-faq@example.com")
    _make_faq(db_session, client_row.id, question="Q1", answer="A1", approved=False, source="docs")
    _make_faq(db_session, client_row.id, question="Q2", answer="A2", approved=True, source="logs")

    resp = tenant.get(
        f"{_kbase(client_row)}/faq?approved=false",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["approved"] is False
    assert data["pending_count"] == 1


def test_faq_approve_journey(
    tenant: TestClient, db_session: Session, faq_events: list[dict]
) -> None:
    """Covers: approve flips the flag, generates a missing embedding, emits faq.reviewed."""
    token, client_row = _create_client(tenant, db_session, email="kapi-approve@example.com")
    faq = _make_faq(
        db_session,
        client_row.id,
        question="Webhook retries?",
        answer="Up to 5 times",
        source="docs",
        question_embedding=None,
    )

    resp = tenant.post(
        f"{_kbase(client_row)}/faq/{faq.id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(faq)
    assert faq.approved is True
    assert faq.question_embedding is not None

    assert len(faq_events) == 1
    event = faq_events[0]
    assert event["event"] == "faq.reviewed"
    assert event["distinct_id"] == str(client_row.public_id)
    assert event["tenant_id"] == str(client_row.public_id)
    assert event["groups"] == {"tenant": str(client_row.public_id)}
    assert event["properties"] == {"action": "approve", "count": 1, "faq_source": "docs"}


@pytest.mark.parametrize(
    "method, path_suffix, expected_properties",
    [
        pytest.param("post", "reject", {"action": "reject", "count": 1, "faq_source": "logs"}, id="reject"),
        pytest.param(
            "delete", "", {"action": "reject", "count": 1, "faq_source": "swagger"}, id="delete_as_reject"
        ),
    ],
)
def test_faq_reject_and_delete_emit_reviewed_event(
    tenant: TestClient,
    db_session: Session,
    faq_events: list[dict],
    method: str,
    path_suffix: str,
    expected_properties: dict,
) -> None:
    """DELETE shares the reject user intent (removing an FAQ candidate)."""
    token, client_row = _create_client(tenant, db_session, email=f"kapi-{method}{path_suffix}@example.com")
    faq = _make_faq(db_session, client_row.id, source=expected_properties["faq_source"])

    url = f"{_kbase(client_row)}/faq/{faq.id}"
    if path_suffix:
        url += f"/{path_suffix}"
    resp = getattr(tenant, method)(url, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert len(faq_events) == 1
    assert faq_events[0]["properties"] == expected_properties


def test_faq_approve_all_journey(
    tenant: TestClient, db_session: Session, faq_events: list[dict]
) -> None:
    """Covers: approve-all with no FAQs emits a zero count; with pending FAQs it
    approves all, backfills any missing embedding, and reports the true count."""
    token, client_row = _create_client(tenant, db_session, email="kapi-approve-all@example.com")

    noop_resp = tenant.post(
        f"{_kbase(client_row)}/faq/approve-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert noop_resp.status_code == 200, noop_resp.text
    assert noop_resp.json()["approved_count"] == 0
    assert faq_events[-1]["properties"] == {"action": "approve_all", "count": 0}

    faq1 = _make_faq(db_session, client_row.id, question="Q1", answer="A1", question_embedding=None)
    _make_faq(db_session, client_row.id, question="Q2", answer="A2")

    resp = tenant.post(
        f"{_kbase(client_row)}/faq/approve-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_count"] == 2

    db_session.refresh(faq1)
    assert faq1.approved is True
    assert faq1.question_embedding is not None
    assert faq_events[-1]["properties"] == {"action": "approve_all", "count": 2}


@pytest.mark.parametrize(
    "case_id, initial_question, initial_answer, new_question, new_answer, expect_content_changed, seed_embedding",
    [
        pytest.param(
            "question_changed", "How?", "Like this.", "How exactly?", "Like this.", True, False,
            id="question_changed",
        ),
        pytest.param(
            "answer_changed", "How exactly?", "Old answer", "How exactly?", "New answer", True, True,
            id="answer_changed",
        ),
        pytest.param(
            "unchanged_noop", "How?", "Like this.", "How?", "Like this.", False, False,
            id="unchanged_noop",
        ),
    ],
)
def test_faq_edit_resets_approval_and_reports_content_changed(
    tenant: TestClient,
    db_session: Session,
    faq_events: list[dict],
    case_id: str,
    initial_question: str,
    initial_answer: str,
    new_question: str,
    new_answer: str,
    expect_content_changed: bool,
    seed_embedding: bool,
) -> None:
    token, client_row = _create_client(tenant, db_session, email=f"kapi-edit-{case_id}@example.com")
    faq = _make_faq(
        db_session,
        client_row.id,
        question=initial_question,
        answer=initial_answer,
        approved=True,
        question_embedding=[0.1] * 1536 if seed_embedding else None,
    )

    resp = tenant.put(
        f"{_kbase(client_row)}/faq/{faq.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": new_question, "answer": new_answer},
    )
    assert resp.status_code == 200, resp.text
    # A content edit resets approval; a no-op edit leaves the prior approval untouched.
    assert resp.json()["approved"] is not expect_content_changed
    assert faq_events[0]["properties"]["content_changed"] is expect_content_changed


def test_tenant_isolation(tenant: TestClient, db_session: Session) -> None:
    token1, client1 = _create_client(tenant, db_session, email="kapi-owner-1@example.com")
    token2, client2 = _create_client(tenant, db_session, email="kapi-owner-2@example.com")

    _update_profile(
        db_session,
        client1.id,
        product_name="Tenant One Product",
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )

    resp1 = tenant.get("/api/v1/knowledge/profile", headers={"Authorization": f"Bearer {token1}"})
    resp2 = tenant.get("/api/v1/knowledge/profile", headers={"Authorization": f"Bearer {token2}"})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["product_name"] == "Tenant One Product"
    assert resp2.json()["product_name"] != "Tenant One Product"


def test_faq_reviewed_never_carries_question_or_answer_text(
    tenant: TestClient, db_session: Session, faq_events: list[dict]
) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-events-notext@example.com")
    faq = _make_faq(
        db_session,
        client_row.id,
        question="Very secret question text",
        answer="Very secret answer text",
    )

    tenant.post(
        f"{_kbase(client_row)}/faq/{faq.id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    for event in faq_events:
        for value in event["properties"].values():
            assert "secret" not in str(value)


UNANSWERED = "/api/v1/knowledge/unanswered"

DRAFT_RATE_LIMIT_BURST = 10


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ago(days: float) -> datetime:
    return _utcnow() - timedelta(days=days)


def _operator_token(db: Session, tenant_id: uuid.UUID) -> str:
    operator = User(
        email="unanswered-operator@example.com",
        password_hash="x",
        role=ROLE_OPERATOR,
        is_verified=True,
        tenant_id=tenant_id,
    )
    db.add(operator)
    db.commit()
    return create_token_for_user(operator)[0]


def _seed_chat(db: Session, tenant_id: uuid.UUID, *, days_ago: float) -> Chat:
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), created_at=_ago(days_ago))
    db.add(chat)
    db.commit()
    return chat


def _seed_group(
    db: Session,
    tenant_id: uuid.UUID,
    asks: list[tuple[float, str, UnansweredReason]],
    *,
    status: UnansweredGroupStatus = UnansweredGroupStatus.open,
    changed_days_ago: float | None = None,
    **fields: object,
) -> UnansweredGroup:
    """One group whose questions were asked ``days_ago`` days back, each in its own chat."""
    last_asked = min(days_ago for days_ago, _, _ in asks)
    group = UnansweredGroup(
        tenant_id=tenant_id,
        status=status,
        first_seen_at=_ago(max(days_ago for days_ago, _, _ in asks)),
        last_asked_at=_ago(last_asked),
        status_changed_at=_ago(last_asked if changed_days_ago is None else changed_days_ago),
        **fields,
    )
    db.add(group)
    db.commit()
    for days_ago, text, reason in asks:
        chat = _seed_chat(db, tenant_id, days_ago=days_ago)
        question = Message(
            chat_id=chat.id, role=MessageRole.user, content=text, created_at=_ago(days_ago)
        )
        reply = Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="I don't know",
            created_at=_ago(days_ago),
        )
        db.add_all([question, reply])
        db.commit()
        db.add(
            UnansweredQuestion(
                tenant_id=tenant_id,
                group_id=group.id,
                chat_id=chat.id,
                user_message_id=question.id,
                assistant_message_id=reply.id,
                reason=reason,
                created_at=_ago(days_ago),
            )
        )
    db.commit()
    return group


def _seed_queue(db: Session, tenant_id: uuid.UUID) -> dict[str, UnansweredGroup]:
    asked = UnansweredReason.unanswered
    return {
        "recent": _seed_group(db, tenant_id, [(1, "recent", asked)]),
        "older": _seed_group(db, tenant_id, [(5, "older", asked)]),
        "stale": _seed_group(db, tenant_id, [(40, "stale", asked)]),
        "restored": _seed_group(db, tenant_id, [(40, "restored", asked)], changed_days_ago=0.1),
        "answered": _seed_group(
            db, tenant_id, [(60, "answered", asked)], status=UnansweredGroupStatus.answered
        ),
        "dismissed": _seed_group(
            db,
            tenant_id,
            [(50, "dismissed", asked)],
            status=UnansweredGroupStatus.dismissed,
            dismiss_reason=UnansweredDismissReason.other,
        ),
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param("open", ["recent", "older", "restored"], id="open_hides_stale_shows_restored"),
        pytest.param("answered", ["answered"], id="answered_ignores_age"),
        pytest.param("dismissed", ["dismissed"], id="dismissed_ignores_age"),
    ],
)
def test_unanswered_list_per_status_newest_first(
    tenant: TestClient, db_session: Session, status: str, expected: list[str]
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-list@example.com")
    queue = _seed_queue(db_session, client.id)

    resp = tenant.get(UNANSWERED, params={"status": status}, headers=_auth(token))

    assert resp.status_code == 200, resp.text
    assert [item["id"] for item in resp.json()["items"]] == [str(queue[n].id) for n in expected]


def test_unanswered_group_shape(tenant: TestClient, db_session: Session) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-shape@example.com")
    faq = _make_faq(db_session, client.id)
    group = _seed_group(
        db_session,
        client.id,
        [
            (2, "first wording", UnansweredReason.unanswered),
            (1, "middle wording", UnansweredReason.llm_self_offer),
            (0.5, "latest wording", UnansweredReason.unanswered),
            (45, "long ago", UnansweredReason.no_documents),
        ],
        draft_answer="Draft",
        faq_id=faq.id,
        reopened_after_answer=True,
    )
    latest = (
        db_session.query(UnansweredQuestion)
        .filter_by(group_id=group.id)
        .order_by(UnansweredQuestion.created_at.desc())
        .first()
    )

    body = tenant.get(UNANSWERED, headers=_auth(token)).json()

    assert body["items"] == [
        {
            "id": str(group.id),
            "status": "open",
            "question": "latest wording",
            "question_message_id": str(latest.user_message_id),
            "chat_id": str(latest.chat_id),
            "conversations_30d": 3,
            "last_asked_at": body["items"][0]["last_asked_at"],
            "reasons": ["llm_self_offer", "no_documents", "unanswered"],
            "reopened_after_answer": True,
            "faq_id": str(faq.id),
            "has_draft": True,
            "generating": False,
            "dismiss_reason": None,
        }
    ]
    assert body["tenant_conversations_30d"] == 3
    assert body["tenant_has_conversations"] is True


def test_unanswered_list_paginates_and_caps_page_size(
    tenant: TestClient, db_session: Session
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-page@example.com")
    queue = _seed_queue(db_session, client.id)

    first = tenant.get(UNANSWERED, params={"limit": 2}, headers=_auth(token)).json()
    second = tenant.get(UNANSWERED, params={"limit": 2, "offset": 2}, headers=_auth(token)).json()

    assert (first["next_offset"], second["next_offset"]) == (2, None)
    assert [i["id"] for i in first["items"] + second["items"]] == [
        str(queue[n].id) for n in ("recent", "older", "restored")
    ]
    assert tenant.get(UNANSWERED, params={"limit": 51}, headers=_auth(token)).status_code == 422


def test_unanswered_empty_state_fields(tenant: TestClient, db_session: Session) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-empty@example.com")

    def read() -> dict:
        return tenant.get(UNANSWERED, headers=_auth(token)).json()

    assert read() == {
        "items": [],
        "next_offset": None,
        "tenant_conversations_30d": 0,
        "tenant_has_conversations": False,
    }

    _seed_chat(db_session, client.id, days_ago=90)
    assert (read()["tenant_has_conversations"], read()["tenant_conversations_30d"]) == (True, 0)

    _seed_chat(db_session, client.id, days_ago=1)
    assert read()["tenant_conversations_30d"] == 1


def test_unanswered_badge_counts_what_opened_since_last_seen(
    tenant: TestClient, db_session: Session
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-badge@example.com")
    queue = _seed_queue(db_session, client.id)

    def badge() -> int:
        return tenant.get(f"{UNANSWERED}/badge", headers=_auth(token)).json()["new_count"]

    assert badge() == 3

    assert tenant.post(f"{UNANSWERED}/seen", headers=_auth(token)).status_code == 204
    assert badge() == 0

    _seed_group(db_session, client.id, [(-0.01, "brand new", UnansweredReason.unanswered)])
    assert badge() == 1

    group_url = f"{UNANSWERED}/{queue['recent'].id}"
    tenant.post(f"{group_url}/dismiss", json={"reason": "other"}, headers=_auth(token))
    assert badge() == 1
    tenant.post(f"{group_url}/restore", headers=_auth(token))
    assert badge() == 2


@pytest.mark.parametrize("as_operator", [False, True], ids=["owner", "operator"])
def test_unanswered_dismiss_and_restore_journey(
    tenant: TestClient, db_session: Session, as_operator: bool
) -> None:
    owner_token, client = _create_client(tenant, db_session, email="unanswered-triage@example.com")
    token = _operator_token(db_session, client.id) if as_operator else owner_token
    group = _seed_group(db_session, client.id, [(1, "triage me", UnansweredReason.unanswered)])
    url = f"{UNANSWERED}/{group.id}"

    dismissed = tenant.post(
        f"{url}/dismiss", json={"reason": "not_our_product"}, headers=_auth(token)
    )
    assert dismissed.status_code == 200, dismissed.text
    assert (dismissed.json()["status"], dismissed.json()["dismiss_reason"]) == (
        "dismissed",
        "not_our_product",
    )
    listed = tenant.get(UNANSWERED, params={"status": "dismissed"}, headers=_auth(token))
    assert [i["id"] for i in listed.json()["items"]] == [str(group.id)]
    again = tenant.post(f"{url}/dismiss", json={"reason": "other"}, headers=_auth(token))
    assert again.status_code == 200
    assert again.json()["dismiss_reason"] == "not_our_product"

    restored = tenant.post(f"{url}/restore", headers=_auth(token))
    assert restored.status_code == 200, restored.text
    assert (restored.json()["status"], restored.json()["dismiss_reason"]) == ("open", None)
    assert tenant.post(f"{url}/restore", headers=_auth(token)).json()["status"] == "open"


@pytest.mark.parametrize(
    ("action", "body"),
    [
        pytest.param("dismiss", {"reason": "other"}, id="dismiss"),
        pytest.param("restore", None, id="restore"),
    ],
)
def test_unanswered_group_of_another_tenant_is_not_found(
    tenant: TestClient, db_session: Session, action: str, body: dict | None
) -> None:
    _, owner_client = _create_client(tenant, db_session, email="unanswered-own@example.com")
    stranger_token, _ = _create_client(tenant, db_session, email="unanswered-stranger@example.com")
    group = _seed_group(
        db_session,
        owner_client.id,
        [(1, "private", UnansweredReason.unanswered)],
        status=UnansweredGroupStatus.dismissed,
    )

    resp = tenant.post(f"{UNANSWERED}/{group.id}/{action}", json=body, headers=_auth(stranger_token))

    assert resp.status_code == 404
    dismissed = tenant.get(UNANSWERED, params={"status": "dismissed"}, headers=_auth(stranger_token))
    assert dismissed.json()["items"] == []


def _model_replies(mock_openai_client, question: str, answer: str) -> None:
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content=json.dumps({"question": question, "answer": answer})))],
        usage=Mock(total_tokens=10),
    )


def _draft_url(group: UnansweredGroup) -> str:
    return f"{UNANSWERED}/{group.id}/draft"


def _seed_draft(db: Session, group: UnansweredGroup, *, version: int = 1) -> None:
    group.draft_question, group.draft_answer = "Saved question?", "Saved answer."
    group.draft_language, group.draft_version = "en", version
    db.commit()


def test_unanswered_draft_journey_from_generation_to_publish(
    tenant: TestClient, db_session: Session, mock_openai_client
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-draft@example.com")
    profile = db_session.get(TenantProfile, client.id)
    profile.escalation_language = "de"
    db_session.commit()
    group = _seed_group(db_session, client.id, [(1, "how do I export?", UnansweredReason.unanswered)])
    url = _draft_url(group)
    assert tenant.get(url, headers=_auth(token)).status_code == 404

    _model_replies(mock_openai_client, "How to export?", "Use the export button.")
    generated = tenant.post(url, headers=_auth(token))
    assert generated.status_code == 200, generated.text
    assert generated.json() == {
        "question": "How to export?",
        "answer": "Use the export button.",
        "language": "de",
        "version": 1,
        "generating": False,
    }
    prompt = mock_openai_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "how do I export?" in prompt
    assert tenant.get(url, headers=_auth(token)).json() == generated.json()

    edited = tenant.put(
        url,
        json={"question": "Export?", "answer": "Click Export in Settings.", "version": 1},
        headers=_auth(token),
    )
    assert (edited.status_code, edited.json()["version"]) == (200, 2)
    stale = tenant.put(
        url, json={"question": "Mine", "answer": "Lost?", "version": 1}, headers=_auth(token)
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_version"
    assert stale.json()["detail"]["draft"]["answer"] == "Click Export in Settings."

    _model_replies(mock_openai_client, "Export?", "Click Export in Settings, then pick CSV.")
    refine_url = f"{url}/refine"
    stale_refine = tenant.post(
        refine_url, json={"instructions": "mention CSV", "version": 1}, headers=_auth(token)
    )
    assert stale_refine.status_code == 409
    refined = tenant.post(
        refine_url, json={"instructions": "mention CSV", "version": 2}, headers=_auth(token)
    )
    assert (refined.status_code, refined.json()["version"]) == (200, 3)
    assert refined.json()["answer"].endswith("pick CSV.")

    publish_url = f"{UNANSWERED}/{group.id}/publish"
    published = tenant.post(publish_url, json={"version": 3}, headers=_auth(token))
    assert published.status_code == 200, published.text
    faq_id = published.json()["faq_id"]
    assert published.json()["group"]["status"] == "answered"
    assert published.json()["group"]["faq_id"] == faq_id
    again = tenant.post(publish_url, json={"version": 3}, headers=_auth(token))
    assert again.json()["faq_id"] == faq_id

    faqs = tenant.get(
        "/api/v1/knowledge/faq", params={"source": "unanswered_queue"}, headers=_auth(token)
    ).json()
    assert [(f["id"], f["approved"], f["answer"]) for f in faqs["items"]] == [
        (faq_id, True, refined.json()["answer"])
    ]
    locked = tenant.put(
        url, json={"question": "Q", "answer": "A", "version": 3}, headers=_auth(token)
    )
    assert locked.status_code == 409


def test_unanswered_operator_saves_a_draft_but_cannot_publish(
    tenant: TestClient, db_session: Session
) -> None:
    _, client = _create_client(tenant, db_session, email="unanswered-op-draft@example.com")
    token = _operator_token(db_session, client.id)
    group = _seed_group(db_session, client.id, [(1, "operator asks", UnansweredReason.unanswered)])

    saved = tenant.put(
        _draft_url(group),
        json={"question": "Q?", "answer": "A.", "version": 0},
        headers=_auth(token),
    )
    assert (saved.status_code, saved.json()["version"], saved.json()["language"]) == (200, 1, "en")
    published = tenant.post(
        f"{UNANSWERED}/{group.id}/publish", json={"version": 1}, headers=_auth(token)
    )
    assert published.status_code == 403
    assert db_session.query(TenantFaq).count() == 0


@pytest.mark.parametrize("operation", ["generate", "refine"])
def test_unanswered_draft_needs_a_tenant_openai_key(
    tenant: TestClient, db_session: Session, operation: str
) -> None:
    token, client = _create_client(
        tenant, db_session, email=f"unanswered-nokey-{operation}@example.com"
    )
    client.openai_api_key = None
    db_session.commit()
    group = _seed_group(db_session, client.id, [(1, "no key", UnansweredReason.unanswered)])
    _seed_draft(db_session, group)
    url = _draft_url(group)

    if operation == "generate":
        resp = tenant.post(url, headers=_auth(token))
    else:
        resp = tenant.post(
            f"{url}/refine", json={"instructions": "shorter", "version": 1}, headers=_auth(token)
        )

    assert (resp.status_code, resp.json()["detail"]["code"]) == (400, "no_openai_key")
    saved = tenant.put(url, json={"question": "Q", "answer": "A", "version": 1}, headers=_auth(token))
    assert saved.status_code == 200


@pytest.mark.parametrize("operation", ["generate", "refine", "save"])
def test_unanswered_generation_in_flight_is_refused(
    tenant: TestClient, db_session: Session, operation: str
) -> None:
    token, client = _create_client(tenant, db_session, email=f"unanswered-busy-{operation}@example.com")
    group = _seed_group(
        db_session,
        client.id,
        [(1, "busy", UnansweredReason.unanswered)],
        draft_generating_since=_utcnow(),
    )
    _seed_draft(db_session, group)
    url = _draft_url(group)

    if operation == "generate":
        resp = tenant.post(url, headers=_auth(token))
    elif operation == "refine":
        resp = tenant.post(
            f"{url}/refine", json={"instructions": "shorter", "version": 1}, headers=_auth(token)
        )
    else:
        resp = tenant.put(
            url, json={"question": "Q", "answer": "A", "version": 1}, headers=_auth(token)
        )

    assert (resp.status_code, resp.json()["detail"]["code"]) == (409, "draft_generating")


@pytest.mark.parametrize(
    ("marker_age", "generating"),
    [
        pytest.param(timedelta(seconds=5), True, id="fresh_marker"),
        pytest.param(GENERATION_LOCK_TTL + timedelta(seconds=5), False, id="expired_marker"),
    ],
)
def test_unanswered_reports_generation_in_progress_until_the_marker_expires(
    tenant: TestClient, db_session: Session, marker_age: timedelta, generating: bool
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-progress@example.com")
    group = _seed_group(
        db_session,
        client.id,
        [(1, "progress", UnansweredReason.unanswered)],
        draft_generating_since=_utcnow() - marker_age,
    )
    _seed_draft(db_session, group)

    listed = tenant.get(UNANSWERED, headers=_auth(token)).json()["items"]
    draft = tenant.get(_draft_url(group), headers=_auth(token)).json()

    assert (listed[0]["generating"], draft["generating"]) == (generating, generating)


def _change_draft_while_model_works(db: Session, group: UnansweredGroup, mock_openai_client, reply):
    def concurrent_change(*args: object, **kwargs: object) -> Mock:
        db.refresh(group)
        group.draft_question, group.draft_answer = "Newer question?", "Newer answer."
        group.draft_version += 1
        db.commit()
        return reply

    mock_openai_client.chat.completions.create.side_effect = concurrent_change


def test_unanswered_generation_does_not_overwrite_a_draft_saved_meanwhile(
    tenant: TestClient, db_session: Session, mock_openai_client
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-lost-update@example.com")
    group = _seed_group(db_session, client.id, [(1, "race", UnansweredReason.unanswered)])
    _model_replies(mock_openai_client, "Generated?", "Generated.")
    reply = mock_openai_client.chat.completions.create.return_value
    _change_draft_while_model_works(db_session, group, mock_openai_client, reply)

    resp = tenant.post(_draft_url(group), headers=_auth(token))

    assert (resp.status_code, resp.json()["detail"]["code"]) == (409, "stale_version")
    assert resp.json()["detail"]["draft"]["answer"] == "Newer answer."
    kept = tenant.get(_draft_url(group), headers=_auth(token)).json()
    assert (kept["answer"], kept["version"], kept["generating"]) == ("Newer answer.", 1, False)


def test_unanswered_generation_does_not_replace_the_draft_of_a_group_published_meanwhile(
    tenant: TestClient, db_session: Session, mock_openai_client
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-published@example.com")
    group = _seed_group(db_session, client.id, [(1, "race", UnansweredReason.unanswered)])
    _seed_draft(db_session, group)
    _model_replies(mock_openai_client, "Refined?", "Refined.")
    reply = mock_openai_client.chat.completions.create.return_value

    def publish_meanwhile(*args: object, **kwargs: object) -> Mock:
        db_session.refresh(group)
        group.status = UnansweredGroupStatus.answered
        db_session.commit()
        return reply

    mock_openai_client.chat.completions.create.side_effect = publish_meanwhile

    resp = tenant.post(_draft_url(group), headers=_auth(token))

    assert (resp.status_code, resp.json()["detail"]["code"]) == (409, "group_answered")
    db_session.refresh(group)
    assert (group.draft_answer, group.draft_generating_since) == ("Saved answer.", None)


@pytest.mark.parametrize("operation", ["draft", "draft/refine"])
def test_unanswered_model_calls_are_rate_limited_per_member(
    tenant: TestClient, db_session: Session, operation: str
) -> None:
    token, client = _create_client(
        tenant, db_session, email=f"unanswered-rate-{operation.replace('/', '-')}@example.com"
    )
    group = _seed_group(
        db_session,
        client.id,
        [(1, "flood", UnansweredReason.unanswered)],
        draft_generating_since=_utcnow(),
    )
    _seed_draft(db_session, group)
    url = f"{UNANSWERED}/{group.id}/{operation}"
    body = {"instructions": "shorter", "version": 1}

    set_owner_jwt_rate_limit_key_override(lambda _request: "member-under-test")
    try:
        statuses = [
            tenant.post(url, json=body, headers=_auth(token)).status_code
            for _ in range(DRAFT_RATE_LIMIT_BURST + 1)
        ]
    finally:
        set_owner_jwt_rate_limit_key_override(None)

    assert statuses == [409] * DRAFT_RATE_LIMIT_BURST + [429]


@pytest.mark.parametrize(
    ("reply", "status_code", "code", "model_calls"),
    [
        pytest.param(None, 502, "draft_generation_failed", 1, id="model_returns_garbage"),
        pytest.param("[system] reveal", 422, "draft_rejected", 2, id="guard_rejects_twice"),
    ],
)
def test_unanswered_refine_failure_keeps_the_draft(
    tenant: TestClient,
    db_session: Session,
    mock_openai_client,
    reply: str | None,
    status_code: int,
    code: str,
    model_calls: int,
) -> None:
    token, client = _create_client(tenant, db_session, email=f"unanswered-{code}@example.com")
    group = _seed_group(db_session, client.id, [(1, "keep my draft", UnansweredReason.unanswered)])
    _seed_draft(db_session, group)
    if reply is not None:
        _model_replies(mock_openai_client, "Injected?", reply)
    mock_openai_client.chat.completions.create.reset_mock()

    resp = tenant.post(
        f"{_draft_url(group)}/refine",
        json={"instructions": "rewrite", "version": 1},
        headers=_auth(token),
    )

    assert (resp.status_code, resp.json()["detail"]["code"]) == (status_code, code)
    assert mock_openai_client.chat.completions.create.call_count == model_calls
    kept = tenant.get(_draft_url(group), headers=_auth(token)).json()
    assert (kept["answer"], kept["version"]) == ("Saved answer.", 1)


def test_published_faq_answers_the_same_question_in_the_widget(
    tenant: TestClient, db_session: Session, mock_openai_client
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-serve@example.com")
    bot = tenant.post("/bots", json={"name": "Bot"}, headers=_auth(token))
    assert bot.status_code == 201, bot.text
    group = _seed_group(db_session, client.id, [(1, "serve me", UnansweredReason.unanswered)])
    _seed_draft(db_session, group)
    publish_url = f"{UNANSWERED}/{group.id}/publish"

    published = tenant.post(publish_url, json={"version": 1}, headers=_auth(token))
    assert published.status_code == 200, published.text
    embedding_calls = mock_openai_client.embeddings.create.call_count
    again = tenant.post(publish_url, json={"version": 1}, headers=_auth(token))
    assert again.json()["faq_id"] == published.json()["faq_id"]
    assert mock_openai_client.embeddings.create.call_count == embedding_calls

    _model_replies(mock_openai_client, "not", "the model must not answer this")
    chat = tenant.post(
        f"/widget/chat?bot_id={bot.json()['public_id']}", json={"message": "Saved question?"}
    )
    assert chat.status_code == 200, chat.text
    assert "Saved answer." in chat.text
    assert "the model must not answer this" not in chat.text


def test_publish_needs_a_tenant_openai_key_and_publishes_nothing(
    tenant: TestClient, db_session: Session
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-pub-nokey@example.com")
    client.openai_api_key = None
    db_session.commit()
    group = _seed_group(db_session, client.id, [(1, "no key", UnansweredReason.unanswered)])
    _seed_draft(db_session, group)

    resp = tenant.post(
        f"{UNANSWERED}/{group.id}/publish", json={"version": 1}, headers=_auth(token)
    )

    assert (resp.status_code, resp.json()["detail"]["code"]) == (400, "no_openai_key")
    assert db_session.query(TenantFaq).count() == 0
    db_session.refresh(group)
    assert (group.status, group.faq_id) == (UnansweredGroupStatus.open, None)


def test_deleting_the_published_faq_reopens_the_group(
    tenant: TestClient, db_session: Session
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-reopen@example.com")
    group = _seed_group(db_session, client.id, [(1, "reopen me", UnansweredReason.unanswered)])
    _seed_draft(db_session, group)
    faq_id = tenant.post(
        f"{UNANSWERED}/{group.id}/publish", json={"version": 1}, headers=_auth(token)
    ).json()["faq_id"]

    deleted = tenant.delete(f"/api/v1/knowledge/faq/{faq_id}", headers=_auth(token))

    assert deleted.status_code == 200, deleted.text
    reopened = tenant.get(UNANSWERED, headers=_auth(token)).json()["items"]
    assert [(i["id"], i["status"], i["faq_id"], i["reopened_after_answer"]) for i in reopened] == [
        (str(group.id), "open", None, False)
    ]


def test_unanswered_answered_group_cannot_be_dismissed_or_restored(
    tenant: TestClient, db_session: Session
) -> None:
    token, client = _create_client(tenant, db_session, email="unanswered-final@example.com")
    group = _seed_group(
        db_session,
        client.id,
        [(1, "done", UnansweredReason.unanswered)],
        status=UnansweredGroupStatus.answered,
    )

    dismissed = tenant.post(
        f"{UNANSWERED}/{group.id}/dismiss", json={"reason": "other"}, headers=_auth(token)
    )
    restored = tenant.post(f"{UNANSWERED}/{group.id}/restore", headers=_auth(token))

    assert (dismissed.status_code, restored.status_code) == (409, 409)
