"""``POST /v1/libraries/{libraryId}/search`` (spec §6.3, §8).

Filters are validated here and handed to the query builder as ``SearchFilters``. Dates are
inclusive for the caller: ``authoredBetween`` and ``uploadedBetween`` match timestamps, so their
``to`` becomes an exclusive bound on the following day, as the spec §8 clauses expect. A window
may leave out ``from`` or ``to``; the open side is unbounded.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator

from fli.api.documents import FILE_TYPES
from fli.auth import require_caller
from fli.errors import ApiError
from fli.presentation import CompactResponse, EvidenceBudget, ResponseView, respond
from fli.query import DateRange, SearchFilters
from fli.search import Searcher, SearchRequest

router = APIRouter(prefix="/v1/libraries/{library_id}", dependencies=[Depends(require_caller)])

MAX_VALUES = 20  # per list filter
EARLIEST, LATEST = "0001-01-01", "9999-12-31"

FileType = Literal[*sorted(set(FILE_TYPES.values()))]  # type: ignore[valid-type]
Value = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TagFacet = Annotated[str, StringConstraints(strip_whitespace=True, max_length=301, pattern=r"^[^=]+=.+$")]


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def bounded_and_ordered(self) -> Window:
        if self.start is None and self.end is None:
            raise ValueError("a window needs 'from', 'to' or both")
        if self.start and self.end and self.start > self.end:
            raise ValueError("'from' is after 'to'")
        return self

    def overlap(self) -> DateRange:
        """For ``period``: the document's period overlaps the window, both ends inclusive."""
        start = self.start.isoformat() if self.start else EARLIEST
        return DateRange(start, self.end.isoformat() if self.end else LATEST)

    def timestamps(self) -> DateRange:
        """For timestamps: ``>= from`` and ``< the day after to``."""
        end = (self.end + timedelta(days=1)).isoformat() if self.end else LATEST
        return DateRange(self.start.isoformat() if self.start else EARLIEST, end)

    def describe(self) -> str:
        return f"{self.start.isoformat() if self.start else ''}..{self.end.isoformat() if self.end else ''}"


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Window | None = None
    authoredBetween: Window | None = None
    uploadedBetween: Window | None = None
    authors: list[Value] = Field(default_factory=list, max_length=MAX_VALUES)
    tags: list[TagFacet] = Field(default_factory=list, max_length=MAX_VALUES)
    fileTypes: list[FileType] = Field(default_factory=list, max_length=MAX_VALUES)
    contentKinds: list[Literal["text", "image"]] = Field(default_factory=list, max_length=2)

    def to_search(self) -> SearchFilters:
        return SearchFilters(
            period=self.period.overlap() if self.period else None,
            authored_between=self.authoredBetween.timestamps() if self.authoredBetween else None,
            uploaded_between=self.uploadedBetween.timestamps() if self.uploadedBetween else None,
            authors=tuple(dict.fromkeys(self.authors)),
            tags=tuple(dict.fromkeys(self.tags)),
            file_types=tuple(dict.fromkeys(self.fileTypes)),
            content_kinds=tuple(dict.fromkeys(self.contentKinds)),
        )

    def applied(self) -> dict[str, Any]:
        """``coverage.filtersApplied``: what the caller asked for, in their terms."""
        out: dict[str, Any] = {}
        for name in ("period", "authoredBetween", "uploadedBetween"):
            if (window := getattr(self, name)) is not None:
                out[name] = window.describe()
        for name in ("authors", "tags", "fileTypes", "contentKinds"):
            if values := list(dict.fromkeys(getattr(self, name))):
                out[name] = values
        return out


class DocumentScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documentId: Value
    generation: Annotated[StrictInt, Field(ge=1)] | None = None
    contentRevision: Annotated[StrictInt, Field(ge=0)] | None = None


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    filters: Filters | None = None
    top: StrictInt | None = None
    includeNeighbors: StrictBool = False
    distinctDocuments: StrictBool = False
    document: DocumentScope | None = None


@router.post("/search", response_model=CompactResponse | dict[str, Any])
async def search(
    library_id: str,
    body: SearchBody,
    request: Request,
    x_agent_id: Annotated[str | None, Header()] = None,
    view: ResponseView = "raw",
    maxEvidenceTokens: EvidenceBudget = None,
) -> dict[str, Any]:
    settings = request.app.state.settings
    top = settings.search_default_top if body.top is None else body.top
    if not 1 <= top <= settings.search_max_top:
        raise ApiError(400, "invalid_request", f"top must be from 1 to {settings.search_max_top}")
    if not body.query.strip():
        raise ApiError(400, "invalid_request", "query must not be blank")
    filters = body.filters or Filters()
    searcher: Searcher = request.app.state.searcher
    response = await searcher.search(
        library_id,
        x_agent_id,
        SearchRequest(
            body.query,
            top,
            body.includeNeighbors,
            filters.to_search(),
            filters.applied(),
            body.distinctDocuments,
            body.document.model_dump(exclude_none=True) if body.document else None,
        ),
    )

    return await respond(response, view, maxEvidenceTokens)
