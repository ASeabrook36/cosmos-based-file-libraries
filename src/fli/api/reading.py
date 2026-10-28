"""Agent-facing catalogue, complete-text reader and transport-neutral tool bundle."""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator

from fli.api.search import DocumentScope, Filters, Value
from fli.auth import require_caller
from fli.errors import ApiError
from fli.metrics import record as record_metric
from fli.page_images import read_page_images
from fli.permissions import check_search_allowed
from fli.presentation import CompactResponse, EvidenceBudget, ResponseView, respond
from fli.reading import DEFAULT_READ_TOKENS, MAX_READ_TOKENS, manifest, read_documents, render_catalog
from fli.search import QueryCost, SearchRequest
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
    mode: Literal["catalogue", "semantic_candidates"] = "catalogue"
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)] | None = None
    filters: DocumentFilters = Field(default_factory=DocumentFilters)
    fileNames: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]] = Field(
        default_factory=list, max_length=20
    )
    fileNameContains: Value | None = None
    after: Value | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50


class Selection(DocumentScope):
    sectionIds: list[Value] = Field(default_factory=list, max_length=100)
    evidenceIds: list[Value] = Field(default_factory=list, max_length=20)
    pages: list[Annotated[StrictInt, Field(ge=1, le=2000)]] = Field(default_factory=list, max_length=20)
    slides: list[Annotated[StrictInt, Field(ge=1, le=10000)]] = Field(default_factory=list, max_length=20)
    paragraphs: list[Annotated[StrictInt, Field(ge=1, le=100000)]] = Field(default_factory=list, max_length=20)


class ReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["full", "outline", "selected"] = "full"
    afterSection: Annotated[StrictInt, Field(ge=0, le=100000)] = 0
    sectionLimit: Annotated[StrictInt, Field(ge=1, le=200)] = 100
    documents: list[Selection] = Field(min_length=1, max_length=20)
    maxTokens: Annotated[StrictInt, Field(ge=1, le=MAX_READ_TOKENS)] = DEFAULT_READ_TOKENS
    includeText: StrictBool = True

    @model_validator(mode="after")
    def unique_documents(self) -> ReadBody:
        if len({s.documentId for s in self.documents}) != len(self.documents):
            raise ValueError("documents must have distinct documentIds")
        for selection in self.documents:
            has_scope = any(
                getattr(selection, k) for k in ("sectionIds", "evidenceIds", "pages", "slides", "paragraphs")
            )
            if has_scope != (self.mode == "selected"):
                raise ValueError("source selectors are required only in selected mode")
        return self


class ImagesBody(DocumentScope):
    pages: list[Annotated[StrictInt, Field(ge=1, le=2000)]] = Field(default_factory=list, max_length=3)
    imageIds: list[Value] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def distinct_selectors(self):
        if bool(self.pages) == bool(self.imageIds):
            raise ValueError("choose pages or imageIds")
        if len(set(self.pages)) != len(self.pages) or len(set(self.imageIds)) != len(self.imageIds):
            raise ValueError("selectors must be distinct")
        return self


@router.post("/documents:images", response_model=CompactResponse | dict[str, Any])
async def images(
    library_id: str,
    body: ImagesBody,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
    view: ResponseView = "raw",
    maxEvidenceTokens: EvidenceBudget = None,
):
    response = await read_page_images(
        request.app.state.searcher,
        request.app.state.blobs,
        library_id,
        x_agent_id,
        body.documentId,
        body.generation,
        body.pages,
        image_ids=body.imageIds,
        content_revision=body.contentRevision,
    )

    return await respond(response, view, maxEvidenceTokens)


@router.get("/tools")
async def get_tools(
    library_id: str,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    library = await request.app.state.libraries.get(library_id)
    check_search_allowed(library_id, library, x_agent_id)
    return {"tools": tool_definitions(library)}


@router.post("/documents:discover", response_model=CompactResponse | dict[str, Any])
async def discover(
    library_id: str,
    body: DiscoverBody,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
    view: ResponseView = "raw",
    maxEvidenceTokens: EvidenceBudget = None,
) -> dict[str, Any]:
    if body.mode == "semantic_candidates":
        if not body.query or body.fileNames or body.fileNameContains or body.after:
            raise ApiError(
                400, "invalid_request", "semantic candidates need query, without catalogue name/paging selectors"
            )
        response = await request.app.state.searcher.search(
            library_id,
            x_agent_id,
            SearchRequest(
                body.query,
                min(body.limit, 20),
                filters=body.filters.to_search(),
                filters_applied=body.filters.applied(),
                distinct_documents=True,
            ),
        )
        rows = []
        for hit in response["results"]:
            rows.append(
                {
                    k: hit[k]
                    for k in (
                        "documentId",
                        "generation",
                        "contentRevision",
                        "fileName",
                        "documentTokens",
                        "representation",
                    )
                }
            )
        response = {
            **response,
            "documents": rows,
            "disposition": response["selectionStatus"],
            "coverage": {**response["coverage"], "disposition": response["selectionStatus"]},
            "results": [],
            "hasMore": False,
            "nextAfter": None,
            "exhaustive": False,
            "nextAction": "Search evidence within a selected document using document selector.",
        }
        return await respond(response, view, maxEvidenceTokens)
    if body.query is not None:
        raise ApiError(400, "invalid_request", "query requires mode=semantic_candidates")
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
    response = {
        "library": {"libraryId": library_id, "name": library.get("name")},
        "mode": "catalogue",
        "resultUnit": "documents",
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

    return await respond(response, view, maxEvidenceTokens)


@router.post("/documents:read", response_model=CompactResponse | dict[str, Any])
async def read(
    library_id: str,
    body: ReadBody,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
    view: ResponseView = "raw",
    maxEvidenceTokens: EvidenceBudget = None,
) -> dict[str, Any]:
    return await read_documents(
        request.app.state.searcher,
        request.app.state.blobs,
        library_id,
        x_agent_id,
        [(s.documentId, s.generation) for s in body.documents],
        min(body.maxTokens, maxEvidenceTokens)
        if view == "compact" and maxEvidenceTokens is not None
        else body.maxTokens,
        body.includeText,
        mode=body.mode,
        scopes={s.documentId: s.model_dump(exclude_none=True, exclude_defaults=True) for s in body.documents},
        after_section=body.afterSection,
        section_limit=body.sectionLimit,
        view=view,
    )
