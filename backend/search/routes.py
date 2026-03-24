"""FastAPI search endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from openai import APIError, APITimeoutError
from sqlalchemy.orm import Session

from backend.auth.middleware import get_current_user
from backend.clients.service import get_client_by_user
from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.models import User
from backend.search.schemas import SearchRequest, SearchResponse, SearchResultItem
from backend.search.service import search_similar_chunks

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

    try:
        results_tuples = search_similar_chunks(
            client_id=client.id,
            query=body.query,
            top_k=body.top_k,
            db=db,
            api_key=client.openai_api_key,
        )
    except (APIError, APITimeoutError) as exc:
        logger.warning("OpenAI API error during search: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="OpenAI service unavailable",
        )

    items = [
        SearchResultItem(
            document_id=emb.document_id,
            chunk_text=emb.chunk_text,
            similarity=round(similarity, 6),
            chunk_index=emb.metadata_json.get("chunk_index", -1),
        )
        for emb, similarity in results_tuples
    ]

    return SearchResponse(results=items)
