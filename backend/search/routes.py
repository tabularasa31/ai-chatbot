"""FastAPI search endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from openai import APIError
from sqlalchemy.orm import Session

from backend.auth.middleware import get_current_user
from backend.clients.service import get_client_by_user
from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.models import User
from backend.search.schemas import SearchRequest, SearchResponse, SearchResultItem
from backend.observability import begin_trace
from backend.search.service import (
    build_reliability_projection,
    build_variant_trace_metadata,
    build_variant_trace_tag,
    search_similar_chunks_detailed,
)

search_router = APIRouter(tags=["search"])
logger = logging.getLogger(__name__)


@limiter.limit("30/minute")
@search_router.post("", response_model=SearchResponse)
def search_route(
    request: Request,
    body: SearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SearchResponse:
    """
    Vector similarity search over embeddings (protected JWT).

    Embeds the query, searches across client's embeddings, returns top_k results.
    Errors: 401 (no/invalid JWT), 404 (user has no client), 503 (OpenAI unavailable).
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if not client.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    trace = begin_trace(
        name="search-request",
        session_id=f"search:{uuid.uuid4()}",
        client_id=str(client.id),
        user_id=str(current_user.id),
        metadata={
            "client_id": str(client.id),
            "user_id": str(current_user.id),
            "route": str(request.url.path),
            "top_k": body.top_k,
        },
        tags=[f"tenant:{client.id}"],
    )

    try:
        bundle = search_similar_chunks_detailed(
            client_id=client.id,
            query=body.query,
            top_k=body.top_k,
            db=db,
            api_key=client.openai_api_key,
            trace=trace,
        )
    except APIError as exc:
        logger.warning("OpenAI API error during search: %s", exc)
        trace.update(
            output={"error": True},
            metadata={"route": str(request.url.path)},
            level="ERROR",
            status_message=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        )

    results_tuples = bundle.results

    items = [
        SearchResultItem(
            document_id=emb.document_id,
            chunk_text=emb.chunk_text,
            similarity=round(similarity, 6),
            chunk_index=emb.metadata_json.get("chunk_index", -1),
        )
        for emb, similarity in results_tuples
    ]

    trace.update(
        output={"result_count": len(items)},
        metadata={
            "route": str(request.url.path),
            "search_result_count": len(items),
            **build_reliability_projection(bundle.reliability),
            **build_variant_trace_metadata(bundle),
        },
        tags=[build_variant_trace_tag(bundle.variant_mode)],
    )

    return SearchResponse(results=items)
