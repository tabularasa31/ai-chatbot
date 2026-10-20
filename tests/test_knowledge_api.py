from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
from backend.models.base import _utcnow
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
    assert again.status_code == 409

    restored = tenant.post(f"{url}/restore", headers=_auth(token))
    assert restored.status_code == 200, restored.text
    assert (restored.json()["status"], restored.json()["dismiss_reason"]) == ("open", None)
    assert tenant.post(f"{url}/restore", headers=_auth(token)).status_code == 409


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
