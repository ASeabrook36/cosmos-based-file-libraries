"""``POST /v1/libraries/{libraryId}/search`` (spec §6.3, §8)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from fli.auth import require_caller
from fli.errors import ApiError
from fli.search import Searcher, SearchRequest

router = APIRouter(prefix="/v1/libraries/{library_id}", dependencies=[Depends(require_caller)])


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    filters: dict[str, Any] | None = None
    top: StrictInt | None = None
    includeNeighbors: StrictBool = False


@router.post("/search")
async def search(
    library_id: str,
    body: SearchBody,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    settings = request.app.state.settings
    top = settings.search_default_top if body.top is None else body.top
    if not 1 <= top <= settings.search_max_top:
        raise ApiError(400, "invalid_request", f"top must be from 1 to {settings.search_max_top}")
    if body.filters and any(v not in (None, [], {}) for v in body.filters.values()):
        raise ApiError(400, "filters_not_supported", "metadata filters arrive in a later phase; search without them")
    if not body.query.strip():
        raise ApiError(400, "invalid_request", "query must not be blank")
    searcher: Searcher = request.app.state.searcher
    return await searcher.search(library_id, x_agent_id, SearchRequest(body.query, top, body.includeNeighbors))
