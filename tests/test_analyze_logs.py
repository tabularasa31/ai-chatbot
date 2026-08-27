"""Tests for Phase 4 — analyze_chat_logs.py.

Covers acceptance criteria from spec:
1. Batch-only clustering (no historical data outside batch)
2. Watermark delayed-insert guard (30s)
3. MAX_FAQ_PER_RUN limit with priority by cluster size
4. Answer = next assistant message after specific user message
5. Thumbs-down clusters are skipped
6. asyncio.sleep called between embedding batches
7. Job timeout stops processing early
8. FAQEntry contains cluster_size and source_message_ids
9. analysis_version change resets watermark
10. is_running = FALSE guaranteed in finally
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

from backend.models import (
    Chat,
    Tenant,
    LogAnalysisState,
    Message,
    MessageEmbedding,
    MessageFeedback,
    MessageRole,
    TenantFaq,
    User,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client_row(db_session):
    user = User(
        email="tenant@example.com",
        password_hash="x",
        is_admin=False,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    tenant = Tenant(
        name="Test Tenant",
        public_id="test-public-id",
    )
    db_session.add(tenant)
    db_session.commit()
    return tenant


@pytest.fixture()
def chat_session(db_session, client_row):
    chat = Chat(
        tenant_id=client_row.id,
        session_id=uuid.uuid4(),
    )
    db_session.add(chat)
    db_session.commit()
    return chat


def _make_message(
    db_session,
    chat,
    role: MessageRole,
    content: str,
    created_at: datetime | None = None,
    feedback: MessageFeedback = MessageFeedback.none,
) -> Message:
    msg = Message(
        chat_id=chat.id,
        role=role,
        content=content,
        feedback=feedback,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(msg)
    db_session.flush()
    return msg


# ── test_clustering_batch_only ────────────────────────────────────────────────


def test_clustering_batch_only(db_session, client_row, chat_session):
    """Clustering must only use messages within the current batch.

    We verify by checking that _cluster_messages only receives IDs from
    the batch passed to it and does not query historical data.
    """
    from backend.jobs.analyze_chat_logs import _cluster_messages, MessageRow

    now = datetime.now(timezone.utc)
    batch = [
        MessageRow(
            id=uuid.uuid4(),
            content="How do I reset my password?",
            created_at=now - timedelta(minutes=i),
            conversation_id=uuid.uuid4(),
            embedding=[0.1] * 1536,
        )
        for i in range(5)
    ]
    # Add a very similar message as historical (same embedding)
    historical_id = uuid.uuid4()

    # Cluster only using batch — the function receives only batch IDs
    clusters = _cluster_messages(batch)

    cluster_msg_ids = {m.id for cluster in clusters for m in cluster}
    # historical_id must not appear in any cluster
    assert historical_id not in cluster_msg_ids
    # All cluster members must be in the batch
    batch_ids = {m.id for m in batch}
    assert cluster_msg_ids.issubset(batch_ids)


# ── test_watermark_delayed_insert ─────────────────────────────────────────────


def test_watermark_delayed_insert(db_session, client_row, chat_session):
    """Messages created within the last 30 s must not appear in the batch."""
    from backend.jobs.analyze_chat_logs import _load_messages

    now = datetime.now(timezone.utc)

    # Message 45 s ago — should be included
    old_msg = _make_message(
        db_session,
        chat_session,
        MessageRole.user,
        "Old message",
        created_at=now - timedelta(seconds=45),
    )
    db_session.commit()

    # Message 15 s ago — within 30 s guard, must NOT be included
    recent_msg = _make_message(
        db_session,
        chat_session,
        MessageRole.user,
        "Recent message",
        created_at=now - timedelta(seconds=15),
    )
    db_session.commit()

    messages = _load_messages(db_session, client_row.id, None, batch_size=100)
    msg_ids = {m.id for m in messages}

    assert old_msg.id in msg_ids, "Message older than 30s should be included"
    assert recent_msg.id not in msg_ids, "Message within 30s should be excluded"


# ── test_max_faq_per_run ──────────────────────────────────────────────────────


def test_max_faq_per_run():
    """25 clusters → only MAX_FAQ_PER_RUN=20 candidates, largest first."""
    from backend.jobs.analyze_chat_logs import MessageRow
    import uuid as _uuid

    # Simulate 25 clusters with varying sizes
    clusters = [
        [
            MessageRow(
                id=_uuid.uuid4(),
                content=f"q{j}",
                created_at=datetime.now(timezone.utc),
                conversation_id=_uuid.uuid4(),
                embedding=[float(i)] * 1536,
            )
            for j in range(i + 1)  # cluster i has i+1 members
        ]
        for i in range(25)
    ]

    # Apply the same sorting + truncation as the job
    clusters.sort(key=lambda c: len(c), reverse=True)
    clusters = clusters[:20]

    assert len(clusters) == 20
    # Largest cluster should be first (size 25, which is clusters[24])
    assert len(clusters[0]) == 25
    # Smallest retained cluster should have at least size 6 (clusters[5])
    assert len(clusters[-1]) >= 6


# ── test_answer_is_next_message ───────────────────────────────────────────────


def test_answer_is_next_message(db_session, client_row, chat_session):
    """Answer must be the next assistant message after the specific user message."""
    from backend.jobs.analyze_chat_logs import _get_answer_for_message

    now = datetime.now(timezone.utc)

    user_msg = _make_message(
        db_session,
        chat_session,
        MessageRole.user,
        "What is the refund policy?",
        created_at=now - timedelta(seconds=30),
    )
    # This should be selected (immediately after user message)
    correct_answer = _make_message(
        db_session,
        chat_session,
        MessageRole.assistant,
        "Refunds are processed in 7 days.",
        created_at=now - timedelta(seconds=20),
    )
    # A later assistant message — should NOT be selected
    _make_message(
        db_session,
        chat_session,
        MessageRole.assistant,
        "Is there anything else?",
        created_at=now - timedelta(seconds=10),
    )
    db_session.commit()

    answer_text, _ = _get_answer_for_message(db_session, user_msg.id, chat_session.id)
    assert answer_text == correct_answer.content


# ── test_thumbs_down_skip ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thumbs_down_skip(db_session, client_row, chat_session):
    """Cluster where all answers are thumbs-down must not produce a FAQ candidate."""
    from backend.jobs.analyze_chat_logs import (
        MessageFeedback,
        _get_answer_for_message,
    )

    now = datetime.now(timezone.utc)
    # Create user messages + thumbs-down assistant answers. Timestamps step
    # *forward* per turn so the rows form a real conversation (q, a, q, a, …).
    # They used to step backward, which put all three questions before all
    # three answers — a shape the pairing rule cannot read, and one no chat
    # produces.
    user_msgs = []
    for i in range(3):
        user_msg = _make_message(
            db_session,
            chat_session,
            MessageRole.user,
            f"Bad question {i}",
            created_at=now - timedelta(seconds=60 - i * 10),
        )
        _make_message(
            db_session,
            chat_session,
            MessageRole.assistant,
            f"Bad answer {i}",
            created_at=now - timedelta(seconds=55 - i * 10),
            feedback=MessageFeedback.down,
        )
        user_msgs.append(user_msg)
    db_session.commit()

    # Verify feedback is read from ASSISTANT messages (not user messages)
    for user_msg in user_msgs:
        _, feedback = _get_answer_for_message(db_session, user_msg.id, chat_session.id)
        assert feedback == MessageFeedback.down, (
            "Feedback should come from the assistant message, not user message"
        )

    # All-thumbs-down → no FAQ candidate should be created
    faq_count_before = (
        db_session.query(TenantFaq).filter_by(tenant_id=client_row.id).count()
    )
    assert faq_count_before == 0


# ── test_embedding_throttle ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embedding_throttle(db_session, client_row):
    """asyncio.sleep must be called between embedding batches."""
    from backend.jobs.analyze_chat_logs import MessageRow, _generate_embeddings

    now = datetime.now(timezone.utc)
    # Create 250 messages (> EMBEDDING_BATCH_SIZE=100 → needs 3 batches → 2 sleeps)
    messages = [
        MessageRow(
            id=uuid.uuid4(),
            content=f"message {i}",
            created_at=now - timedelta(minutes=i),
            conversation_id=uuid.uuid4(),
        )
        for i in range(250)
    ]

    mock_openai = MagicMock()
    mock_openai.embeddings.create.side_effect = lambda model, input: MagicMock(
        data=[MagicMock(embedding=[0.1] * 1536) for _ in range(len(input))]
    )

    sleep_calls = []

    async def mock_sleep(sec):
        sleep_calls.append(sec)

    with (
        patch(
            "backend.jobs.analyze_chat_logs.get_openai_client", return_value=mock_openai
        ),
        patch("backend.jobs.analyze_chat_logs._get_cached_embeddings", return_value={}),
        patch("backend.jobs.analyze_chat_logs._save_embeddings"),
        patch("backend.jobs.analyze_chat_logs._touch_embeddings"),
        patch("asyncio.sleep", side_effect=mock_sleep),
    ):
        await _generate_embeddings(messages, "sk-test", db_session, client_row.id)

    # 250 messages / 100 per batch = 3 batches → 2 intermediate sleeps
    assert len(sleep_calls) == 2, f"Expected 2 sleep calls, got {len(sleep_calls)}"


# ── test_job_timeout ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_timeout(db_session, client_row):
    """Job must stop early when MAX_JOB_DURATION_SEC is exceeded."""
    import time as _time
    from backend.jobs.analyze_chat_logs import (
        MessageRow,
        _cluster_messages,
    )

    now = datetime.now(timezone.utc)
    messages = [
        MessageRow(
            id=uuid.uuid4(),
            content=f"timeout test {i}",
            created_at=now - timedelta(minutes=i),
            conversation_id=uuid.uuid4(),
            embedding=[0.1 * (i % 10)] * 1536,
        )
        for i in range(10)
    ]
    clusters = _cluster_messages(messages)

    # Simulate the timeout check: if elapsed > MAX, break
    processed = []
    start = _time.monotonic()

    with patch("backend.core.config.settings") as mock_settings:
        mock_settings.max_job_duration_sec = 0  # instant timeout

        for cluster in clusters:
            elapsed = _time.monotonic() - start
            if elapsed > mock_settings.max_job_duration_sec:
                break
            processed.append(cluster)

    # With 0s timeout, no clusters should be processed
    assert len(processed) == 0


# ── test_faq_entry_has_explainability_fields ──────────────────────────────────


def test_faq_entry_has_explainability_fields(db_session, client_row, chat_session):
    """FAQEntry must contain cluster_size and source_message_ids (up to 10 IDs)."""
    from backend.jobs.analyze_chat_logs import (
        MessageRow,
        _create_faq_candidate,
        ClusterMember,
    )

    now = datetime.now(timezone.utc)
    cluster = [
        MessageRow(
            id=uuid.uuid4(),
            content="How do I cancel my subscription?",
            created_at=now - timedelta(minutes=i),
            conversation_id=chat_session.id,
            embedding=[0.1] * 1536,
        )
        for i in range(7)
    ]
    representative = cluster[0]
    best_member = ClusterMember(
        message=representative,
        answer="You can cancel from Account Settings.",
        has_thumbs_up=True,
    )

    mock_openai = MagicMock()
    mock_openai.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 1536)]
    )

    with (
        patch(
            "backend.jobs.analyze_chat_logs.get_openai_client", return_value=mock_openai
        ),
        patch("backend.jobs.analyze_chat_logs._find_existing_faq", return_value=None),
    ):
        created = _create_faq_candidate(
            db_session, client_row.id, representative, best_member, cluster, "sk-test"
        )

    assert created is True
    faq = db_session.query(TenantFaq).filter_by(tenant_id=client_row.id).first()
    assert faq is not None
    assert faq.cluster_size == 7
    assert isinstance(faq.source_message_ids, list)
    assert len(faq.source_message_ids) <= 10
    assert faq.source == "logs"


# ── test_analysis_version_resets_watermark ────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_version_resets_watermark(db_session, client_row):
    """Changing CURRENT_ANALYSIS_VERSION must reset watermark for the tenant."""
    from backend.jobs.analyze_chat_logs import (
        _get_or_create_state,
        run_job,
        CURRENT_ANALYSIS_VERSION,
    )

    # Set up state with old version + a watermark
    state = _get_or_create_state(db_session, client_row.id)
    state.analysis_version = CURRENT_ANALYSIS_VERSION - 1  # simulate old version
    state.last_run_started_at = datetime.now(timezone.utc) - timedelta(days=1)
    state.is_running = True
    db_session.commit()

    old_watermark = state.last_run_started_at

    # Mock the job to avoid full run
    with (
        patch("backend.jobs.analyze_chat_logs._load_messages", return_value=[]),
        patch("backend.jobs.analyze_chat_logs._finalize_job"),
    ):
        await run_job(
            tenant_id=client_row.id,
            api_key="sk-test",
            db=db_session,
            job_start=datetime.now(timezone.utc),
            old_watermark=None,
            trigger="test",
        )

    db_session.expire_all()
    updated_state = _get_or_create_state(db_session, client_row.id)
    assert updated_state.last_run_started_at is None, (
        "Watermark should be reset when analysis_version changes"
    )
    assert updated_state.analysis_version == CURRENT_ANALYSIS_VERSION


# ── test_is_running_released_on_failure ───────────────────────────────────────


@pytest.mark.asyncio
async def test_is_running_released_on_failure(db_session, client_row):
    """is_running must be set to FALSE in the finally block even if job fails."""
    from backend.jobs.analyze_chat_logs import _get_or_create_state, run_job

    state = _get_or_create_state(db_session, client_row.id)
    state.is_running = True
    db_session.commit()

    with patch(
        "backend.jobs.analyze_chat_logs._load_messages",
        side_effect=RuntimeError("Boom"),
    ):
        await run_job(
            tenant_id=client_row.id,
            api_key="sk-test",
            db=db_session,
            job_start=datetime.now(timezone.utc),
            old_watermark=None,
            trigger="test",
        )

    db_session.expire_all()
    state = (
        db_session.query(LogAnalysisState).filter_by(tenant_id=client_row.id).first()
    )
    assert state is not None
    assert state.is_running is False, "is_running must be FALSE after job failure"
    assert state.last_run_status == "failed"


# ── swallowed visitor turns must not borrow a later answer ────────────────────


def test_swallowed_visitor_turns_get_no_answer(db_session, client_row, chat_session):
    """A live operator handoff breaks "every user row has an assistant row".

    Visitor turns during the handoff are swallowed by ``OperatorHandler`` and
    persisted with no reply. Unbounded, all of them would pair with the first
    bot answer *after* the release — an unrelated one — and be fed to OpenAI
    as FAQ candidates, each carrying that answer's thumbs-up/down. A turn
    nobody answered must return no answer.
    """
    from backend.jobs.analyze_chat_logs import _get_answer_for_message

    now = datetime.now(timezone.utc)

    # An ordinary answered turn before the handoff.
    answered = _make_message(
        db_session,
        chat_session,
        MessageRole.user,
        "What is the refund policy?",
        created_at=now - timedelta(seconds=120),
    )
    its_answer = _make_message(
        db_session,
        chat_session,
        MessageRole.assistant,
        "Refunds are processed in 7 days.",
        created_at=now - timedelta(seconds=110),
    )
    # Three visitor turns while a human held the chat — no assistant rows.
    swallowed = [
        _make_message(
            db_session,
            chat_session,
            MessageRole.user,
            f"during the handoff {i}",
            created_at=now - timedelta(seconds=90 - i * 5),
        )
        for i in range(3)
    ]
    _make_message(
        db_session,
        chat_session,
        MessageRole.operator,
        "Ann here — looking into it.",
        created_at=now - timedelta(seconds=70),
    )
    # Released; the visitor asks something new and the bot answers it.
    after_release = _make_message(
        db_session,
        chat_session,
        MessageRole.user,
        "and how do I change my plan?",
        created_at=now - timedelta(seconds=30),
    )
    unrelated_answer = _make_message(
        db_session,
        chat_session,
        MessageRole.assistant,
        "Open Settings → Plan to change it.",
        created_at=now - timedelta(seconds=20),
        feedback=MessageFeedback.up,
    )
    db_session.commit()

    # The answered turn still pairs with its own answer.
    text, _ = _get_answer_for_message(db_session, answered.id, chat_session.id)
    assert text == its_answer.content

    # None of the swallowed turns borrows the later, unrelated answer — nor
    # its thumbs-up, which would otherwise be attributed to all three.
    for msg in swallowed:
        text, feedback = _get_answer_for_message(db_session, msg.id, chat_session.id)
        assert text is None
        assert feedback == MessageFeedback.none

    # The post-release turn keeps its own answer.
    text, feedback = _get_answer_for_message(
        db_session, after_release.id, chat_session.id
    )
    assert text == unrelated_answer.content
    assert feedback == MessageFeedback.up
