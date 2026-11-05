"""Readable, turn-local presentation over authorized granular evidence.

This module neither fetches nor caches source bodies. A caller-owned registry
stores exact actions; every invocation still performs the ordinary authorized
read. Raw/canonical evidence is untouched and remains available through details.
"""

from __future__ import annotations

import copy
import hashlib
import json

from fli.selector_hints import GUIDANCE, inspection_hints
from fli_demo.navigation_refs import ReferenceError, validate_reference

CONTRACT = "fli.navigation.v1"
INSTRUCTIONS = (
    "Granular navigation replies use readable labels and exact turn-local action references. "
    "Copy an actions value into the read tool's reference argument; compatible selected-read references "
    "may be combined with references. A paragraph union does not establish whole-cell completeness. "
    "References never authorize access or drop snapshot checks, and expire outside this turn. Rediscover "
    "after stale/unknown references; never construct a reference. documentRef identifies the pinned document; "
    "fileName, paths, source coordinates, citations and applicable limitations accompany each response. "
    "documentInfo appears on its first retained occurrence and again if it changes. "
    "For exact original identifiers or full metadata, reread the same reference with details=true. "
    "navigationPreview is only an exact bounded navigation prefix, even when truncated=false; read its source "
    "before answering and cite the returned citation. Original source bodies and list/cell relationships are "
    "preserved. selectionComplete=true covers the requested atoms, not necessarily the document or a container. "
    "A table/cell read already includes its subtree: repeating a complete read with explicit subtree or a "
    "larger budget does not add source; choose another returned action to change scope. "
    "Inspections and refusals deliver no source; disclose omissions and follow their recovery actions. "
    "Keep internal references/identifiers out of the human answer unless requested."
)

_STATIC_NOTES = {
    "Supported original source atoms and authored hierarchy; processingCoverage states source limitations.",
    "navigationPreview is an exact bounded source prefix for navigation only, even when truncated=false. "
    "Copy readSelector to obtain source evidence and its citation before answering.",
    "selectionComplete=true means all requested source atoms were delivered, not the whole document. "
    "For tables/cells, nodeScope=subtree is already the default: repeating the same nodes with explicit "
    "subtree or a larger budget does not expand a complete selection. Use another returned selector "
    "to change scope.",
}
_INSPECTION_ACTION = inspection_hints({}, {})["nextAction"]
_ACTIONS = {
    "readSelector": "read",
    "outlineSelector": "outline",
    "parentSelector": "parent",
    "containingSectionSelector": "section",
    "subtreeSelector": "subtree",
    "fullSelector": "full",
}
_PIN = ("documentId", "generation", "contentRevision")
_READ_FIELDS = {"mode", "documents", "includeText", "maxTokens", "afterSection", "sectionLimit"}
_ROW_IDS = {"sectionId", "containingSectionId", "navigationNodeId", "parentNodeId", "evidenceId"}
_LOCATION_IDS = {"nodeId", "parentNodeId", "sourceAtomId", "evidenceId"}
_SCOPE_IDS = {*_PIN, "nodeIds", "outlineNodeId", "sectionIds", "evidenceIds"}
_INFO = {
    "generation",
    "contentRevision",
    "sourceSha256",
    "fileType",
    "status",
    "statusDetail",
    "readable",
    "author",
    "authoredAt",
    "uploadedAt",
    "periodStart",
    "periodEnd",
    "tags",
    "description",
    "descriptionSource",
    "representation",
    "chunkCount",
    "imageCount",
    "imagesSkipped",
    "sourceImageCount",
    "sourceImagesSkipped",
    "imageStatus",
    "imagePendingCount",
    "visualsComplete",
    "documentTokens",
    "documentTokenEstimateScope",
    "compactDocumentTokens",
    "compactDocumentTokenEstimateScope",
    "compactDocumentTokenTokenizer",
    "readCapabilities",
}


def _pin(document):
    return {key: document[key] for key in _PIN}


def _valid_pin(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("documentId"), str)
        and bool(value["documentId"])
        and type(value.get("generation")) is int
        and value["generation"] > 0
        and type(value.get("contentRevision")) is int
        and value["contentRevision"] >= 0
    )


def _layers(payload):
    yield payload
    for document in payload["documents"]:
        yield document
        for key in ("outline", "sections"):
            yield from document.get(key, [])


def _supported(payload, library_id):
    if (
        not isinstance(payload, dict)
        or payload.get("mode") not in {"outline", "selected", "full"}
        or not isinstance(payload.get("documents"), list)
        or not payload["documents"]
        or not isinstance(payload.get("library"), dict)
        or payload["library"].get("libraryId") != library_id
    ):
        return False
    pins = {}
    for document in payload["documents"]:
        if (
            not _valid_pin(document)
            or document.get("fileType") not in {"docx", "md", "markdown"}
            or not isinstance(document.get("fileName"), str)
            or not isinstance(document.get("processingCoverage"), dict)
            or document["processingCoverage"].get("sourceStructure") != "fli.granular-source.v1"
            or document.get("candidateImages")
            or document.get("imageCount")
            or document.get("sourceImageCount")
            or not isinstance(document.get("documentSelector"), dict)
            or not isinstance(document.get("fullSelector"), dict)
            or not isinstance(document.get("outlineSelector"), dict)
        ):
            return False
        for key in ("sections", "outline"):
            if key in document and (
                not isinstance(document[key], list)
                or any(
                    not isinstance(row, dict) or row.get("image") or not isinstance(row.get("readSelector"), dict)
                    for row in document[key]
                )
            ):
                return False
        if document["documentId"] in pins:
            return False
        pins[document["documentId"]] = _pin(document)
    for row in _layers(payload):
        if any(key in row for key in ("actions", "documentRef", "documentInfo", "presentation")):
            return False
        if "documentSelector" in row:
            selector = row["documentSelector"]
            if not isinstance(selector, dict) or selector != pins.get(selector.get("documentId")):
                return False
            try:
                validate_reference("document", selector)
            except ReferenceError:
                return False
        for field in _ACTIONS:
            if field not in row:
                continue
            selector = row[field]
            if (
                not isinstance(selector, dict)
                or set(selector) - _READ_FIELDS
                or selector.get("mode") not in {"outline", "selected", "full"}
                or not isinstance(selector.get("documents"), list)
                or not selector["documents"]
                or any(not _valid_pin(p) or _pin(p) != pins.get(p["documentId"]) for p in selector["documents"])
            ):
                return False
            try:
                validate_reference("read", selector)
            except ReferenceError:
                return False
    return True


def _human_context(value):
    result = copy.deepcopy(value)
    if isinstance(result, dict):
        result.pop("nodeId", None)
        if isinstance(result.get("authoredPath"), list):
            result["authoredPath"] = [
                {k: v for k, v in entry.items() if k != "nodeId"} if isinstance(entry, dict) else entry
                for entry in result["authoredPath"]
            ]
    return result


def _pagination(document, request):
    if not document.get("hasMore") or not isinstance(request, dict) or request.get("mode") != "outline":
        return None
    if set(request) - (_READ_FIELDS | {"details"}) or type(document.get("nextAfterSection")) is not int:
        return None
    if not isinstance(request.get("documents"), list) or any(not isinstance(p, dict) for p in request["documents"]):
        return None
    if type(request.get("afterSection", 0)) is not int:
        return None
    matches = [p for p in request["documents"] if p.get("documentId") == document["documentId"]]
    if len(matches) != 1 or document["nextAfterSection"] <= request.get("afterSection", 0):
        return None
    if matches[0].get("outlineNodeId", "n0") != document.get("outlineRootNodeId"):
        return None
    pin = _pin(document)
    if any(key in matches[0] and matches[0][key] != pin[key] for key in _PIN):
        return None
    # Only advance the server-provided page boundary of this exact requested
    # outline. Bind an initially unpinned bootstrap to its validated response.
    selector = {
        **{k: copy.deepcopy(v) for k, v in request.items() if k != "details"},
        "documents": [{**copy.deepcopy(matches[0]), **pin}],
        "afterSection": document["nextAfterSection"],
    }
    try:
        validate_reference("read", selector)
    except ReferenceError:
        return None
    return selector


def present(payload, *, library_id, register, introduced, request=None):
    """Project known granular reads; retain unknown features and raw diagnostics.

    ``introduced`` is rebuilt for each complete retained-transcript render. It
    never determines permission or source validity. ``register`` stores an exact
    read request/document pin under the platform-bound library and returns a ref.
    """
    if request is not None and not isinstance(request, dict):
        return copy.deepcopy(payload)
    if (request or {}).get("details") is True or not _supported(payload, library_id):
        return copy.deepcopy(payload)
    pages = {d["documentId"]: _pagination(d, request) for d in payload["documents"]}
    if any(d.get("hasMore") and pages[d["documentId"]] is None for d in payload["documents"]):
        return copy.deepcopy(payload)
    result = copy.deepcopy(payload)
    result["presentation"] = CONTRACT
    result["library"].pop("libraryId", None)
    if result.get("selectorGuidance") == GUIDANCE:
        result.pop("selectorGuidance")
    if result.get("nextAction") == _INSPECTION_ACTION:
        result["nextAction"] = _INSPECTION_ACTION.replace("readSelector", "actions.read reference")
    if isinstance(result.get("notes"), list):
        result["notes"] = [note for note in result["notes"] if not isinstance(note, str) or note not in _STATIC_NOTES]

    def actions(value):
        emitted = {}
        for field, name in _ACTIONS.items():
            if field in value:
                emitted[name] = register("read", library_id, value.pop(field))
        value.pop("documentSelector", None)
        if emitted:
            value["actions"] = emitted

    actions(result)
    for document in result["documents"]:
        original_id = document["documentId"]
        pin = _pin(document)
        document["documentRef"] = register("document", library_id, pin)
        information = {key: document.pop(key) for key in tuple(document) if key in _INFO}
        fingerprint = hashlib.sha256(
            json.dumps(
                [library_id, pin, document["fileName"], information], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        if fingerprint not in introduced:
            document["documentInfo"] = information
            introduced[fingerprint] = True
        document.pop("documentId")
        actions(document)
        if pages[original_id] is not None:
            document.setdefault("actions", {})["nextOutline"] = register("read", library_id, pages[original_id])
        if isinstance(document.get("scope"), dict):
            document["scope"] = {key: value for key, value in document["scope"].items() if key not in _SCOPE_IDS}
        document.pop("outlineRootNodeId", None)
        if "outlineContext" in document:
            document["outlineContext"] = _human_context(document["outlineContext"])
        if document.get("outlineScope") == "immediate_children; use each outlineSelector to drill down":
            document["outlineScope"] = "immediate_children; use each actions.outline reference to drill down"
        if isinstance(document.get("selectionSummary"), dict):
            summary = document["selectionSummary"]
            if isinstance(summary.get("nodes"), list):
                summary["nodes"] = [_human_context(node) for node in summary["nodes"]]
        for key in ("outline", "sections"):
            for row in document.get(key, []):
                actions(row)
                if isinstance(row.get("authoredPath"), list):
                    row["authoredPath"] = _human_context({"authoredPath": row["authoredPath"]})["authoredPath"]
                for field in _ROW_IDS:
                    row.pop(field, None)
                if isinstance(row.get("location"), dict):
                    row["location"] = {k: v for k, v in row["location"].items() if k not in _LOCATION_IDS}
    return result
