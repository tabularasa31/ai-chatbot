from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.clients.widget_chat_gate import (
    WidgetChatClientGateError,
    get_client_eligible_for_widget_chat,
)
from backend.eval.schemas import EvalResultCreateRequest
from backend.models import EvalResult, EvalSession, Tester


def authenticate_tester(username: str, password: str, db: Session) -> Tester | None:
    u = username.strip()
    if not u:
        return None
    tester = db.query(Tester).filter(Tester.username == u).first()
    if not tester or not tester.is_active:
        return None
    if tester.password != password:
        return None
    return tester


def assert_bot_ready_for_widget_chat(bot_id: str, db: Session) -> None:
    """Same preconditions as POST /widget/chat (shared gate in clients.widget_chat_gate)."""
    try:
        get_client_eligible_for_widget_chat(db, bot_id)
    except WidgetChatClientGateError as e:
        if e.reason == WidgetChatClientGateError.NOT_FOUND:
            raise ValueError("bot_not_found") from e
        if e.reason == WidgetChatClientGateError.INACTIVE:
            raise ValueError("bot_inactive") from e
        raise ValueError("bot_openai_not_configured") from e


def create_eval_session(tester_id: uuid.UUID, bot_id: str, db: Session) -> EvalSession:
    assert_bot_ready_for_widget_chat(bot_id, db)
    session = EvalSession(tester_id=tester_id, bot_id=bot_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session_for_tester(
    session_id: uuid.UUID,
    tester_id: uuid.UUID,
    db: Session,
) -> EvalSession | None:
    row = (
        db.query(EvalSession)
        .filter(EvalSession.id == session_id, EvalSession.tester_id == tester_id)
        .first()
    )
    return row


def _normalized_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    t = comment.strip()
    return t or None


def create_eval_result(
    session: EvalSession,
    body: EvalResultCreateRequest,
    db: Session,
) -> EvalResult:
    result = EvalResult(
        session_id=session.id,
        question=body.question,
        bot_answer=body.bot_answer,
        verdict=body.verdict,
        error_category=None if body.verdict == "pass" else body.error_category,
        comment=_normalized_comment(body.comment),
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def list_session_results(session_id: uuid.UUID, db: Session) -> list[EvalResult]:
    return (
        db.query(EvalResult)
        .filter(EvalResult.session_id == session_id)
        .order_by(EvalResult.created_at.asc())
        .all()
    )
