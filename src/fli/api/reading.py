"""Agent-facing catalogue, complete-text reader and transport-neutral tool bundle."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator

from fli.api.search import Filters, Value
from fli.auth import require_caller
from fli.metrics import record as record_metric
from fli.page_images import read_page_images
from fli.permissions import check_search_allowed
from fli.reading import DEFAULT_READ_TOKENS, MAX_READ_TOKENS, manifest, read_documents, render_catalog
from fli.search import QueryCost
from fli.tool import tool_definitions

router = APIRouter(prefix="/v1/libraries/{library_id}", dependencies=[Depends(require_caller)])


class DocumentFilters(Filters):
    @model_validator(mode="after")
    def no_chunk_filter(self) -> DocumentFilters:
        if self.contentKinds:
            raise ValueError("contentKinds selects passages, not whole documents")
        return self


class DiscoverBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: DocumentFilters = Field(default_factory=DocumentFilters)
    fileNames: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]] = Field(
        default_factory=list, max_length=20
    )
    fileNameContains: Value | None = None
    after: Value | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documentId: Value
    generation: Annotated[StrictInt, Field(ge=1)] | None = None


class ReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[Selection] = Field(min_length=1, max_length=20)
    maxTokens: Annotated[StrictInt, Field(ge=1, le=MAX_READ_TOKENS)] = DEFAULT_READ_TOKENS
    includeText: StrictBool = True

    @model_validator(mode="after")
    def unique_documents(self) -> ReadBody:
        if len({s.documentId for s in self.documents}) != len(self.documents):
            raise ValueError("documents must have distinct documentIds")
        return self


class ImagesBody(Selection):
    pages: list[Annotated[StrictInt, Field(ge=1, le=2000)]] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def distinct_pages(self):
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("pages must be distinct")
        return self


@router.post("/documents:images")
async def images(
    library_id: str, body: ImagesBody, request: Request, x_agent_id: Annotated[str | None, Header()] = None
):
    return await read_page_images(
        request.app.state.searcher,
        request.app.state.blobs,
        library_id,
        x_agent_id,
        body.documentId,
        body.generation,
        body.pages,
    )


@router.get("/tools")
async def get_tools(
    library_id: str,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    library = await request.app.state.libraries.get(library_id)
    check_search_allowed(library_id, library, x_agent_id)
    return {"tools": tool_definitions(library)}


@router.post("/documents:discover")
async def discover(
    library_id: str,
    body: DiscoverBody,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    searcher = request.app.state.searcher
    cost = QueryCost()
    library = await searcher.libraries.get(library_id, raw_response_hook=cost)
    check_search_allowed(library_id, library, x_agent_id)
    filters = body.filters.to_search()
    query = render_catalog(library_id, filters, body.fileNames, body.fileNameContains, body.after, body.limit)
    rows = await searcher.documents.discover(library_id, query, cost)
    latest = await searcher.libraries.get(library_id, raw_response_hook=cost)
    check_search_allowed(library_id, latest, x_agent_id)
    more = len(rows) > body.limit
    rows = rows[: body.limit]
    record_metric(
        "discover",
        library_id,
        agentId=x_agent_id,
        resultCount=len(rows),
        requestCharge=round(cost.request_charge, 2),
        totalMs=round((time.perf_counter() - started) * 1000, 1),
    )
    return {
        "library": {"libraryId": library_id, "name": library.get("name")},
        "documents": [manifest(d) for d in rows],
        "hasMore": more,
        "nextAfter": rows[-1]["id"] if more else None,
        "coverage": {
            "filtersApplied": {
                **body.filters.applied(),
                "fileNames": body.fileNames,
                "fileNameContains": body.fileNameContains,
            },
            "notes": [
                "Filters match recorded metadata only: files without the requested author, tags or dates are excluded. "
                "Missing metadata does not establish that a file is unrelated.",
                "Catalogue pages include files not yet readable. Check readable/status before claiming all were read.",
                "Follow nextAfter with the same selectors until hasMore is false to enumerate all matches. "
                "Pages are not a snapshot; repeat discovery if uploads or metadata are changing.",
                "Generated descriptions identify subjects from opening text; they are not full-file evidence.",
            ],
        },
        "usage": {
            "requestCharge": round(cost.request_charge, 2),
            "totalMs": round((time.perf_counter() - started) * 1000, 1),
        },
    }


@router.post("/documents:read")
async def read(
    library_id: str,
    body: ReadBody,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    return await read_documents(
        request.app.state.searcher,
        request.app.state.blobs,
        library_id,
        x_agent_id,
        [(s.documentId, s.generation) for s in body.documents],
        body.maxTokens,
        body.includeText,
    )
