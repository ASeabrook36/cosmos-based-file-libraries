"""Library endpoints (spec §6.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from fli.auth import require_caller
from fli.config import EMBEDDING_MODEL
from fli.errors import ApiError, library_not_found
from fli.ids import new_id
from fli.permissions import check_immutable
from fli.store import Conflict, LibraryStore, Record, public
from fli.tool import tool_definition

router = APIRouter(prefix="/v1/libraries", dependencies=[Depends(require_caller)])

MUTABLE_FIELDS = ("name", "description", "autoAcceptTagProposals")
REPLACE_ATTEMPTS = 3


class LibraryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["shared", "personal"]
    boundAgentId: str | None = Field(default=None, min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    createdBy: str | None = Field(default=None, max_length=200)  # platform principal, for audit only

    @model_validator(mode="after")
    def binding_matches_kind(self) -> LibraryCreate:
        if self.kind == "personal" and not (self.boundAgentId and self.boundAgentId.strip()):
            raise ValueError("a personal library requires boundAgentId")
        if self.kind == "shared" and self.boundAgentId is not None:
            raise ValueError("a shared library cannot have boundAgentId")
        return self


class LibraryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    autoAcceptTagProposals: StrictBool | None = None


def libraries(request: Request) -> LibraryStore:
    return request.app.state.libraries


Libraries = Annotated[LibraryStore, Depends(libraries)]


def now() -> str:
    return datetime.now(UTC).isoformat()


async def load(store: LibraryStore, library_id: str) -> Record:
    record = await store.get(library_id)
    if record is None or record.get("status") == "deleting":
        raise library_not_found(library_id)
    return record


@router.post("", status_code=201)
async def create_library(body: LibraryCreate, request: Request, store: Libraries) -> Record:
    library_id = new_id()
    settings = request.app.state.settings
    record: Record = {
        "id": library_id,
        "libraryId": library_id,
        "kind": body.kind,
        "name": body.name,
        "description": body.description,
        "descriptionProposal": None,
        "autoAcceptTagProposals": body.kind == "personal",
        "tagVocabulary": {},
        "stats": {"documentCount": 0, "chunkCount": 0, "imageCount": 0, "lastIngestedAt": None},
        "embeddingModel": EMBEDDING_MODEL,
        "embeddingDimensions": settings.aoai_embedding_dimensions,
        "status": "active",
        "createdAt": now(),
        "createdBy": body.createdBy,
        "updatedAt": now(),
    }
    if body.kind == "personal":
        record["boundAgentId"] = body.boundAgentId
    return public(await store.create(record))


@router.get("")
async def list_libraries(
    store: Libraries,
    kind: Literal["shared", "personal"] | None = None,
    status: Literal["active", "deleting"] = "active",
) -> dict[str, list[Record]]:
    return {"libraries": [public(r) for r in await store.list(kind=kind, status=status)]}


@router.get("/{library_id}")
async def get_library(library_id: str, store: Libraries) -> Record:
    return public(await load(store, library_id))


@router.patch("/{library_id}")
async def update_library(library_id: str, store: Libraries, body: Annotated[dict[str, Any], Body()]) -> Record:
    check_immutable(body)  # before validation, so the caller learns why, not just "invalid"
    changes = LibraryUpdate.model_validate(body).model_dump(exclude_unset=True)
    for _ in range(REPLACE_ATTEMPTS):
        record = await load(store, library_id)
        record.update(changes, updatedAt=now())
        try:
            return public(await store.replace(record))
        except Conflict:
            continue
    raise ApiError(409, "conflict", "the library changed while it was being updated; retry")


@router.delete("/{library_id}", status_code=202)
async def delete_library(library_id: str, request: Request, store: Libraries) -> Response:
    for _ in range(REPLACE_ATTEMPTS):
        record = await load(store, library_id)
        record.update(status="deleting", updatedAt=now())
        try:
            await store.replace(record)
            break
        except Conflict:
            continue
    else:
        raise ApiError(409, "conflict", "the library changed while it was being deleted; retry")
    await request.app.state.queue.send({"type": "purgeLibrary", "libraryId": library_id}, session_id=library_id)
    return Response(status_code=202)


@router.get("/{library_id}/tool")
async def get_tool(library_id: str, store: Libraries) -> dict[str, Any]:
    return tool_definition(await load(store, library_id))
