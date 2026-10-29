"""Matched library creation and resumable same-byte uploads; point-read experiment ledger.

Experiment control records occupy their own documents partition, never a real library.
No cross-library chunk queries or new Cosmos container/index policy are introduced.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from fli.api.documents import UploadMetadata, file_type, read_file
from fli.api.libraries import LibraryCreate, library_record, now
from fli.auth import require_caller
from fli.checkpoints import digest
from fli.errors import ApiError
from fli.ids import original_blob_path, safe_file_name
from fli.ingestion import job, profile
from fli.store import Conflict, public

router = APIRouter(prefix="/v1/experiments", dependencies=[Depends(require_caller)])


def ledger_partition(experiment_id: str) -> str:
    return "experiment:" + experiment_id


def stable_id(experiment_id: str, key: str) -> str:
    return str(uuid5(UUID(experiment_id), key))


class PairCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experimentId: UUID
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=4000)
    comparison: Literal["complete-strategy", "controlled-component"] = "complete-strategy"
    component: Literal["chunk-boundaries"] | None = None


def validate_comparison(body: PairCreate):
    if (body.comparison == "controlled-component") != (body.component is not None):
        raise ApiError(400, "invalid_request", "controlled comparisons require component=chunk-boundaries")


async def experiment(request, experiment_id):
    record = await request.app.state.documents.get(ledger_partition(experiment_id), "experiment")
    if record is None:
        raise ApiError(404, "experiment_not_found", "experiment does not exist")
    return record


async def mutate(request, experiment_id, change):
    for _ in range(5):
        record = await experiment(request, experiment_id)
        change(record)
        try:
            return await request.app.state.documents.replace(record)
        except Conflict:
            continue
    raise ApiError(409, "conflict", "experiment changed; retry")


@router.post("", status_code=201)
async def create_pair(body: PairCreate, request: Request):
    validate_comparison(body)
    experiment_id = str(body.experimentId)
    identity = body.model_dump(mode="json")
    store = request.app.state.documents
    settings = request.app.state.settings
    recipes = {}
    for key, profile_id in [("custom", "custom-structured-v1"), ("microsoft", "microsoft-indexer-layout-v1")]:
        recipe = profile(profile_id, controlled=body.component is not None)
        recipe["codeRevision"] = settings.ingestion_code_revision
        recipe["embedding"]["modelRevision"] = settings.aoai_embedding_model_revision
        recipes[key] = recipe
    manifest = {
        "id": "experiment",
        "libraryId": ledger_partition(experiment_id),
        "type": "ingestion-experiment-v1",
        "creationFingerprint": digest(identity),
        "comparison": body.comparison,
        "component": body.component,
        "profiles": recipes,
        "createdAt": now(),
        "members": {
            key: {"libraryId": stable_id(experiment_id, key), "profileId": value, "state": "creating"}
            for key, value in [("custom", "custom-structured-v1"), ("microsoft", "microsoft-indexer-layout-v1")]
        },
        "controls": {
            "retrieval": "same API and vector policy; record search mode, budgets and model revision at replay",
            "extraction": "prebuilt-layout both; same pinned workflow and DI API"
            if body.component
            else "native Markdown/DOCX versus prebuilt-layout; not aligned",
            "chunking": "500/50 both" if body.component else "custom 500/50; Microsoft 5000/200",
            "attribution": "section/sentence versus token-window boundaries; hierarchy, proposals and visuals disabled"
            if body.component
            else "end-to-end strategies; multiple stages differ",
            "indexReadiness": "unmeasured",
            "hostingAllocation": None,
        },
    }
    try:
        await store.create(manifest)
    except Conflict:
        existing = await experiment(request, experiment_id)
        if existing["creationFingerprint"] != digest(identity):
            raise ApiError(409, "idempotency_conflict", "experimentId already binds different settings") from None
    for key in manifest["members"]:
        current = await experiment(request, experiment_id)
        member = current["members"][key]
        if member["state"] != "creating":
            continue
        library_id = member["libraryId"]
        lib = library_record(
            LibraryCreate(
                kind="shared", name=body.name, description=body.description, ingestionProfile=member["profileId"]
            ),
            request.app.state.settings,
            library_id,
        )
        lib.update(experimentId=experiment_id, comparisonMember=key)
        if "profiles" not in current:
            raise ApiError(409, "experiment_profile_unavailable", "legacy incomplete pair has no frozen profiles")
        lib["ingestionProfile"] = copy.deepcopy(current["profiles"][key])
        try:
            await request.app.state.libraries.create(lib)
        except Conflict:
            existing = await request.app.state.libraries.get(library_id)
            if existing is None or existing.get("experimentId") != experiment_id:
                raise ApiError(409, "conflict", "pair member identity conflicts") from None
        except Exception:
            # Manifest and stable IDs survive a partial failure; retry completes only missing members.
            continue
        updated = await mutate(
            request,
            experiment_id,
            lambda r, k=key: (
                r["members"][k].update(state="created") if r["members"][k]["state"] == "creating" else None
            ),
        )
        if updated["members"][key]["state"] == "deleted":
            from fli.api.libraries import delete_library

            await delete_library(library_id, request, request.app.state.libraries)
    return await get_experiment(experiment_id, request)


@router.get("/{experiment_id}")
async def get_experiment(experiment_id: str, request: Request):
    record = public(await experiment(request, experiment_id))
    members = {}
    for key, member in record["members"].items():
        lib = await request.app.state.libraries.get(member["libraryId"])
        docs = await request.app.state.documents.list(member["libraryId"], status=None, facet=None) if lib else []
        members[key] = {
            **member,
            "status": lib.get("status") if lib else "unavailable",
            "ingestionProfile": lib.get("ingestionProfile") if lib else None,
            "documents": [
                {
                    "documentId": doc["id"],
                    "sourceId": doc.get("sourceId"),
                    "sha256": doc.get("sha256"),
                    "status": doc.get("status"),
                    "statusDetail": doc.get("statusDetail"),
                    "activeGeneration": doc.get("activeGeneration"),
                    "pendingGeneration": doc.get("pendingGeneration"),
                }
                for doc in docs
            ],
        }
    return {**record, "members": members}


async def mark_member_deleted(request, library):
    if library.get("experimentId"):
        await mutate(
            request,
            library["experimentId"],
            lambda r: r["members"][library["comparisonMember"]].update(state="deleted"),
        )


@router.post("/{experiment_id}/sources/{source_id}", status_code=202)
async def upload_pair(
    experiment_id: str,
    source_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    metadata: Annotated[str | None, Form()] = None,
):
    manifest = await experiment(request, experiment_id)
    try:
        meta = UploadMetadata.model_validate(json.loads(metadata)) if metadata else UploadMetadata()
    except json.JSONDecodeError as exc:
        raise ApiError(400, "invalid_request", "metadata must be JSON") from exc
    data = await read_file(request, file)
    file_name = safe_file_name(file.filename or "")
    content_type = file.content_type or "application/octet-stream"
    binding = {
        "sourceId": str(source_id),
        "sha256": hashlib.sha256(data).hexdigest(),
        "fileName": file_name,
        "contentType": content_type,
        "metadata": meta.model_dump(mode="json"),
    }
    store = request.app.state.documents
    source_record = {
        "id": str(source_id),
        "libraryId": ledger_partition(experiment_id),
        "type": "matched-source-v1",
        "sourceId": str(source_id),
        "sha256": binding["sha256"],
        "fingerprint": digest(binding),
        "createdAt": now(),
    }
    try:
        await store.create(source_record)
    except Conflict:
        previous = await store.get(ledger_partition(experiment_id), str(source_id))
        if previous is None or previous.get("fingerprint") != digest(binding):
            raise ApiError(409, "source_conflict", "sourceId already binds different bytes or metadata") from None
    results = {}
    for key, member in manifest["members"].items():
        lib_id = member["libraryId"]
        doc_id = stable_id(experiment_id, key + ":" + str(source_id))
        results[key] = {"libraryId": lib_id, "documentId": doc_id, "status": "unavailable"}
        # A deleted/missing member or document is never recreated by a retry.
        lib = await request.app.state.libraries.get(lib_id)
        if member["state"] != "created" or not lib or lib.get("status") == "deleting":
            continue
        document = await store.get(lib_id, doc_id)
        receipt_id = "upload:" + doc_id
        receipt = await store.get(ledger_partition(experiment_id), receipt_id)
        if receipt and receipt.get("state") == "deleted":
            results[key]["status"] = "deleted"
            continue
        if document is None and receipt and receipt.get("state") == "created":
            results[key]["status"] = "deleted"
            continue
        if document is None:
            tags = [t.model_dump() | {"source": "user"} for t in meta.tags]
            document = {
                "id": doc_id,
                "documentId": doc_id,
                "libraryId": lib_id,
                "fileName": file_name,
                "fileType": file_type(file_name),
                "contentType": content_type,
                "sizeBytes": len(data),
                "sha256": binding["sha256"],
                "blobPath": original_blob_path(lib_id, doc_id, 1, file_name),
                "status": "queued",
                "generation": 1,
                "pendingGeneration": 1,
                "activeGeneration": None,
                "pendingFile": None,
                "uploadedAt": now(),
                "updatedAt": now(),
                "metadataVersion": 0,
                "author": meta.author,
                "periodStart": binding["metadata"]["periodStart"],
                "periodEnd": binding["metadata"]["periodEnd"],
                "tags": tags,
                "facets": sorted({f"{t['key']}={t['value']}" for t in tags}),
                "sourceId": str(source_id),
                "sourceMetadataFingerprint": digest(binding["metadata"]),
                "experimentId": experiment_id,
                "chunkCount": 0,
                "imageCount": 0,
                "imagesSkipped": 0,
            }
            document["ingestionJob"] = job(lib_id, doc_id, 1, document, lib["ingestionProfile"], 0)
            try:
                document = await store.create(document)
            except Conflict:
                document = await store.get(lib_id, doc_id)
        try:
            try:
                await store.create({"id": receipt_id, "libraryId": ledger_partition(experiment_id), "state": "created"})
            except Conflict:
                pass
            results[key]["status"] = document["status"]
            if document.get("pendingGeneration") != 1 or document.get("status") == "deleting":
                continue
            if not await request.app.state.blobs.create(document["blobPath"], data, content_type):
                if (
                    hashlib.sha256(await request.app.state.blobs.download(document["blobPath"])).hexdigest()
                    != binding["sha256"]
                ):
                    raise ValueError("immutable pair source changed")
            await request.app.state.queue.send(
                {"type": "ingest", "libraryId": lib_id, "documentId": doc_id, "generation": 1}, session_id=lib_id
            )
        except Exception:
            results[key]["status"] = "enqueue_or_storage_failed"
    return {"experimentId": experiment_id, "source": binding, "members": results}


async def mark_source_deleted(store, document):
    exp_id = ledger_partition(document["experimentId"])
    receipt_id = "upload:" + document["id"]
    for _ in range(5):
        receipt = await store.get(exp_id, receipt_id)
        try:
            if receipt is None:
                await store.create({"id": receipt_id, "libraryId": exp_id, "state": "deleted"})
            else:
                receipt["state"] = "deleted"
                await store.replace(receipt)
            return
        except Conflict:
            continue
    raise ApiError(409, "conflict", "source receipt changed; retry deletion")
