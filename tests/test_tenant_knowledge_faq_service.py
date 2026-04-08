from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.models import Client, TenantFaq, User
from backend.tenant_knowledge import faq_service
from backend.tenant_knowledge.schemas import FaqCandidate


def _create_client(db_session: Session, *, email: str) -> uuid.UUID:
    user = User(
        email=email,
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    client = Client(user_id=user.id, name="FAQ Client", api_key="k" * 32)
    db_session.add(client)
    db_session.commit()
    db_session.refresh(client)
    return client.id


def test_insert_new_faq_candidates_skips_low_confidence_and_duplicates(
    db_session: Session,
    mock_openai_client,
    monkeypatch,
) -> None:
    client_id = _create_client(db_session, email="faq-service@example.com")
    mock_openai_client.embeddings.create.reset_mock()
    dedupe_results = iter([False, True])
    monkeypatch.setattr(
        faq_service,
        "_dedupe_existing_faq_by_similarity",
        lambda **kwargs: next(dedupe_results),
    )

    faq_service.insert_new_faq_candidates(
        db=db_session,
        client_id=client_id,
        faq_candidates=[
            FaqCandidate(
                question="How do billing exports work?",
                answer="Billing exports are generated from the exports page.",
                confidence=0.9,
                source="docs",
            ),
            FaqCandidate(
                question="What is this?",
                answer="Too vague to keep.",
                confidence=0.3,
                source="docs",
            ),
            FaqCandidate(
                question="Can confidence be missing?",
                answer="This candidate should be skipped before embedding.",
                confidence=None,
                source="docs",
            ),
            FaqCandidate(
                question="How do billing exports work?",
                answer="A duplicate answer should be skipped.",
                confidence=0.91,
                source="docs",
            ),
        ],
        api_key="test-key",
        document_id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
    )

    rows = db_session.query(TenantFaq).filter(TenantFaq.tenant_id == client_id).all()

    assert len(rows) == 1
    assert rows[0].question == "How do billing exports work?"
    assert mock_openai_client.embeddings.create.call_count == 2
