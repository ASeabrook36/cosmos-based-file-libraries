"""Exact-owned guards for one explicitly authorized granular qualification.

No credential loading, SDK construction, shared queue or automatic cloud action
lives here. The coordinator injects ordinary stores and conditional-delete
callbacks. Private write-ahead events survive ambiguous responses; uncertainty
blocks retries and cleanup instead of being interpreted as absence or ownership.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath

from azure.core.exceptions import ResourceNotFoundError

from fli.checkpoints import digest, encoded
from fli.ids import chunk_id
from fli.store import Conflict, public
from scripts.retrieval_controls import private_path

LIMITS = {"libraries": 3, "documents": 4, "generations": 5, "chunks": 64, "sourceBytes": 1048576}
TERMINAL = {"confirmed", "rejected"}
CHUNK_HASH_FIELDS = ("text", "heading", "location", "imageRef", "imageMeta", "embeddingModel", "chunkingVersion")


class OwnershipError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise OwnershipError(message)


def identifier(value):
    require(isinstance(value, str), "scope ID must be a canonical UUID")
    try:
        require(str(uuid.UUID(value)) == value, "scope ID must be a canonical UUID")
    except ValueError as exc:
        raise OwnershipError("scope ID must be a canonical UUID") from exc
    return value


def semantic_json(value):
    """Normalize JSON's single number type without Python's bool/int equality.

    Cosmos may return an integral JSON number as int after receiving a float.
    No rounding, string coercion, missing-field folding or array reordering is
    permitted. Non-finite numbers are not JSON and fail before any SDK call.
    """
    if type(value) is float:
        require(math.isfinite(value), "non-finite JSON number")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        require(all(type(key) is str for key in value), "JSON object keys must be strings")
        return {key: semantic_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [semantic_json(item) for item in value]
    require(value is None or type(value) in {str, int, bool}, "unsupported JSON value")
    return value


def semantic_record_digest(record):
    return digest(semantic_json(public(record)))


def identity(record, library=False):
    keys = (
        ("id", "libraryId", "kind", "boundAgentId", "createdAt", "createdBy", "ingestionProfile")
        if library
        else (
            "id",
            "libraryId",
        )
    )
    return digest(semantic_json({key: record.get(key) for key in keys}))


class OwnedLedger:
    """One exclusive process, append-only hash-bound complete state after each event."""

    @classmethod
    def create(cls, directory, *, binding, protected_library_ids, live=True):
        root = private_path(directory)
        require(not root.exists(), "ledger requires a new private directory")
        protected = sorted(set(protected_library_ids))
        require(protected and all(isinstance(x, str) and x for x in protected), "protected inventory required")
        root.mkdir(mode=0o700, parents=True)
        (root / "events").mkdir(mode=0o700)
        result = cls(root, binding, protected, live)
        result._state = {
            "schema": "fli.granular-live.ownership.v1",
            "bindingSha256": digest(binding),
            "protectedLibraryIds": protected,
            "live": live,
            "limits": LIMITS,
            "libraries": {},
            "documents": {},
            "generations": [],
            "blobs": {},
            "chunks": {},
            "operations": [],
            "queue": [],
        }
        result._change("created", {}, lambda state: None)
        return result

    @classmethod
    def open(cls, directory, *, binding, protected_library_ids, live=True):
        root = private_path(directory)
        require(root.is_dir() and not root.is_symlink(), "private ledger unavailable")
        result = cls(root, binding, sorted(set(protected_library_ids)), live)
        try:
            for expected, path in enumerate(sorted((root / "events").iterdir()), 1):
                require(path.name == f"{expected:06d}.json" and not path.is_symlink(), "ledger event inventory differs")
                event = json.loads(path.read_text())
                saved = event.pop("sha256")
                require(
                    event["sequence"] == expected and event["previousSha256"] == result._head, "ledger chain differs"
                )
                require(digest(event) == saved, "ledger event hash differs")
                result._state = event["stateAfter"]
                result._sequence, result._head = expected, saved
            require(result._sequence > 0, "empty ledger")
            require(
                result._state["bindingSha256"] == digest(binding)
                and result._state["protectedLibraryIds"] == result.protected
                and result._state["limits"] == LIMITS
                and result._state["live"] is live,
                "ledger runtime/protected/limit binding differs",
            )
            return result
        except BaseException:
            result.close()
            raise

    def __init__(self, root, binding, protected, live):
        self.root, self.protected, self.live = root, protected, live
        self._state, self._sequence, self._head = {}, 0, None
        self._fd = os.open(root / "operation.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self._fd)
            self._fd = None
            raise

    def close(self):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _change(self, kind, details, change):
        require(self._fd is not None, "ledger is closed")
        state = copy.deepcopy(self._state)
        change(state)
        event = {
            "sequence": self._sequence + 1,
            "previousSha256": self._head,
            "at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "details": details,
            "stateAfter": state,
        }
        head = digest(event)
        path = self.root / "events" / f"{event['sequence']:06d}.json"
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(encoded({**event, "sha256": head}) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._state, self._head, self._sequence = state, head, event["sequence"]

    def snapshot(self):
        """Private: contains exact IDs, paths and messages; never publish this object."""
        return {"eventHeadSha256": self._head, "eventCount": self._sequence, **copy.deepcopy(self._state)}

    def summary(self):
        return {
            "eventHeadSha256": self._head,
            "eventCount": self._sequence,
            "reservedLibraries": len(self._state["libraries"]),
            "reservedDocuments": len(self._state["documents"]),
            "reservedGenerations": len(self._state["generations"]),
            "reservedChunks": len(self._state["chunks"]),
            "recordedBlobs": len(self._state["blobs"]),
            "unresolvedOperations": sum(row["status"] not in TERMINAL for row in self._state["operations"]),
            "unfinishedMessages": sum(row["status"] not in {"completed", "cancelled"} for row in self._state["queue"]),
        }

    def assert_settled(self):
        require(not self.summary()["unresolvedOperations"], "ambiguous mutation requires independent reconciliation")

    def key_settled(self, key):
        require(
            all(row["status"] in TERMINAL for row in self._state["operations"] if row["key"] == key),
            "ambiguous or concurrent mutation for exact key",
        )

    def record_hashes(self, key):
        self.key_settled(key)
        hashes = [
            row.get("semanticBodySha256", row["bodySha256"])
            for row in self._state["operations"]
            if row["key"] == key and row["status"] == "confirmed" and "bodySha256" in row
        ]
        require(hashes, "confirmed record body unavailable")
        return hashes

    def record_hash(self, key):
        return self.record_hashes(key)[-1]

    def record_matches(self, key, record, *, latest=False):
        self.key_settled(key)
        rows = [
            row
            for row in self._state["operations"]
            if row["key"] == key and row["status"] == "confirmed" and "bodySha256" in row
        ]
        require(rows, "confirmed record body unavailable")
        if latest:
            rows = rows[-1:]
        return any(
            semantic_record_digest(record) == row["semanticBodySha256"]
            if "semanticBodySha256" in row
            else digest(public(record)) == row["bodySha256"]
            for row in rows
        )

    def _record_payload(self, operation, role, record):
        """Persist raw SDK input/output before confirmation; private, never receipts."""
        row = next(row for row in self._state["operations"] if row["id"] == operation)
        require(row["status"] in {"started", "unknown"}, "record mutation already settled")
        directory = self.root / "records"
        directory.mkdir(mode=0o700, exist_ok=True)
        require(not directory.is_symlink(), "private record directory is a symlink")
        root_descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
        path = directory / f"{operation}-{role}.json"
        content = encoded(record) + b"\n"
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        reference = {"path": str(path.relative_to(self.root)), "sha256": hashlib.sha256(content).hexdigest()}
        self._change(
            "record-payload",
            {"operationId": operation, "role": role, **reference},
            lambda state: (
                next(item for item in state["operations"] if item["id"] == operation)
                .setdefault("recordPayloads", {})
                .update({role: reference})
            ),
        )
        return reference

    def begin_record(self, kind, key, record):
        normalized = semantic_record_digest(record)  # Validate JSON before reserving an operation.
        operation = self.begin(kind, key, bodySha256=digest(public(record)), semanticBodySha256=normalized)
        self._record_payload(operation, "proposed", copy.deepcopy(record))
        return operation

    def validate_record_response(self, operation, record):
        self._record_payload(operation, "response", copy.deepcopy(record))
        row = next(row for row in self._state["operations"] if row["id"] == operation)
        require(semantic_record_digest(record) == row["semanticBodySha256"], "returned record body differs")

    def confirm_reconciled(self, operation, proposed, observed, *, evidence_path, evidence_sha256):
        """Explicitly reconcile one ambiguous record write from independent evidence.

        The coordinator must supply a fresh independent read, and preserve its
        proof outside the repository. This method makes no cloud calls or guesses:
        the original exact proposed hash must match, then every semantic field of
        the observation must match. Existing events and resource ceilings survive.
        """
        rows = [row for row in self._state["operations"] if row["id"] == operation]
        require(len(rows) == 1, "unknown reconciliation operation")
        row = rows[0]
        require(row["status"] == "unknown", "only an ambiguous operation can be reconciled")
        require(
            row["kind"] in {"library-create", "library-replace", "document-create", "document-replace"},
            "reconciliation supports exact record writes only",
        )
        require(
            [
                item["id"]
                for item in self._state["operations"]
                if item["key"] == row["key"] and item["status"] not in TERMINAL
            ]
            == [operation],
            "another mutation for this key remains unresolved",
        )
        require(digest(public(proposed)) == row["bodySha256"], "original proposed body hash differs")
        normalized = semantic_record_digest(proposed)
        require(normalized == semantic_record_digest(observed), "independent observed body differs")
        require(not self.live or observed.get("_etag"), "independent observation ETag missing")
        kind, identifier_ = row["key"].split(":", 1)
        require(proposed.get("id") == identifier_, "reconciliation record ID differs")
        if kind == "document":
            self.document(proposed["libraryId"], identifier_)
            for field in ("generation", "activeGeneration", "pendingGeneration"):
                if proposed.get(field) is not None:
                    require(
                        [identifier_, proposed[field]] in self._state["generations"],
                        "unowned reconciliation generation",
                    )
        else:
            require(kind == "library" and proposed.get("libraryId") == identifier_, "reconciliation library differs")
            known = self._state["libraries"].get(identifier_)
            require(known is not None and identifier_ not in self.protected, "unowned reconciliation library")
            require(identity(proposed, True) == known["identitySha256"], "reconciliation library identity differs")
        evidence = private_path(evidence_path)
        require(evidence.is_file() and not evidence.is_symlink(), "private reconciliation evidence missing")
        require(
            hashlib.sha256(evidence.read_bytes()).hexdigest() == evidence_sha256, "reconciliation evidence hash differs"
        )
        proposed_ref = self._record_payload(operation, "reconciled-proposed", copy.deepcopy(proposed))
        observed_ref = self._record_payload(operation, "reconciled-observed", copy.deepcopy(observed))

        def confirm(state):
            item = next(item for item in state["operations"] if item["id"] == operation)
            item.update(
                status="confirmed",
                semanticBodySha256=normalized,
                reconciliation={
                    "evidenceSha256": evidence_sha256,
                    "proposed": proposed_ref,
                    "observed": observed_ref,
                    "observationEtag": observed.get("_etag"),
                    "originalBodySha256": item["bodySha256"],
                },
            )
            if item["kind"] == "library-create":
                state["libraries"][identifier_]["status"] = "confirmed"
            elif item["kind"] == "document-create":
                state["documents"][identifier_]["status"] = "confirmed"

        self._change("mutation-reconciled", {"operationId": operation, "evidenceSha256": evidence_sha256}, confirm)

    def library(self, library, *, writing=False):
        value = self._state["libraries"].get(library)
        require(value is not None and library not in self.protected, "unowned library")
        require(value["status"] == "confirmed", "library create is not confirmed")
        if writing:
            require(not value.get("deleted"), "deleted library cannot be resurrected")
            self.key_settled("library:" + library)
        return value

    def document(self, library, document, *, writing=False):
        self.library(library, writing=writing)
        value = self._state["documents"].get(document)
        require(value is not None and value["libraryId"] == library, "unowned document")
        if writing:
            require(not value.get("deleted"), "deleted document cannot be resurrected")
            self.key_settled("document:" + document)
        return value

    def reserve_document(self, library, document, generation):
        self.library(library, writing=True)
        identifier(document)
        require(type(generation) is int and generation > 0, "invalid generation")
        existing = self._state["documents"].get(document)
        require(existing is None or existing["libraryId"] == library, "document belongs to another library")
        if existing:
            require(not existing.get("deleted"), "deleted document cannot be resurrected")
        pair = [document, generation]
        if existing and pair in self._state["generations"]:
            return

        def change(state):
            if not existing:
                require(len(state["documents"]) < LIMITS["documents"], "document ceiling")
                state["documents"][document] = {"libraryId": library, "status": "reserved", "deleted": False}
            if pair not in state["generations"]:
                require(len(state["generations"]) < LIMITS["generations"], "generation ceiling")
                state["generations"].append(pair)

        self._change(
            "reserve-document-generation",
            {"libraryId": library, "documentId": document, "generation": generation},
            change,
        )

    def begin(self, kind, key, **details):
        self.key_settled(key)
        operation = uuid.uuid4().hex

        def change(state):
            require(len(state["operations"]) < 1000, "operation ceiling")
            state["operations"].append({"id": operation, "kind": kind, "key": key, "status": "started", **details})

        self._change("mutation-before", {"operationId": operation}, change)
        return operation

    def finish(self, operation, *, status="confirmed", **details):
        require(status in TERMINAL | {"unknown"}, "invalid mutation disposition")

        def change(state):
            row = next(row for row in state["operations"] if row["id"] == operation)
            require(row["status"] == "started", "mutation already settled")
            row.update(status=status, **details)

        self._change("mutation-after", {"operationId": operation, "status": status}, change)


class GuardedLibraries:
    def __init__(self, store, ledger, *, delete_if_unchanged=None):
        require(not ledger.live or delete_if_unchanged is not None, "live library deletion requires ETag callback")
        self.store, self.ledger, self.delete_if_unchanged = store, ledger, delete_if_unchanged

    async def get(self, library_id, **kwargs):
        if library_id not in self.ledger._state["libraries"]:
            return None
        self.ledger.library(library_id)
        row = await self.store.get(library_id, **kwargs)
        if row:
            require(not self.ledger.library(library_id).get("deleted"), "deleted library reappeared")
            require(self.ledger.record_matches("library:" + library_id, row), "library body differs")
            require(
                identity(row, True) == self.ledger.library(library_id)["identitySha256"], "library identity differs"
            )
        return row

    async def create(self, record):
        library = identifier(record["id"])
        require(record.get("libraryId") == library and library not in self.ledger.protected, "library scope collision")
        require(library not in self.ledger._state["libraries"], "library create is single-attempt")
        require(await self.store.get(library) is None, "library already exists; never adopt it")

        def reserve(state):
            require(len(state["libraries"]) < LIMITS["libraries"], "library ceiling")
            state["libraries"][library] = {
                "identitySha256": identity(record, True),
                "createSha256": digest(public(record)),
                "status": "reserved",
                "deleted": False,
            }

        self.ledger._change("reserve-library", {"libraryId": library, "initialPointReadAbsent": True}, reserve)
        operation = self.ledger.begin_record("library-create", "library:" + library, record)
        try:
            result = await self.store.create(record)
            self.ledger.validate_record_response(operation, result)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        self.ledger._change(
            "confirm-library", {"libraryId": library}, lambda s: s["libraries"][library].update(status="confirmed")
        )
        return result

    async def replace(self, record):
        library = record["id"]
        known = self.ledger.library(library, writing=True)
        require(identity(record, True) == known["identitySha256"], "immutable library identity changed")
        operation = self.ledger.begin_record("library-replace", "library:" + library, record)
        try:
            result = await self.store.replace(record)
            self.ledger.validate_record_response(operation, result)
        except Conflict:
            self.ledger.finish(operation, status="rejected")
            raise
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        return result

    async def list(self, *, kind=None, status=None):
        rows = [await self.get(library) for library in self.ledger._state["libraries"]]
        return [
            row
            for row in rows
            if row and (kind is None or row.get("kind") == kind) and (status is None or row.get("status") == status)
        ]

    async def delete(self, library_id):
        self.ledger.library(library_id)
        key = "library:" + library_id
        self.ledger.key_settled(key)
        record = await self.get(library_id)
        if record is None:
            return
        require(self.ledger.record_matches(key, record, latest=True), "latest library body differs")
        require(record.get("status") == "deleting", "library is not fenced for deletion")
        require(not self.ledger.live or record.get("_etag"), "live library ETag missing")
        operation = self.ledger.begin("library-delete", key, etag=record.get("_etag"))
        try:
            if self.delete_if_unchanged:
                await self.delete_if_unchanged(record)
            else:
                await self.store.delete(library_id)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        self.ledger._change(
            "library-deleted", {"libraryId": library_id}, lambda s: s["libraries"][library_id].update(deleted=True)
        )


class GuardedDocuments:
    def __init__(self, store, ledger, *, delete_if_unchanged=None):
        require(not ledger.live or delete_if_unchanged is not None, "live document deletion requires ETag callback")
        self.store, self.ledger, self.delete_if_unchanged = store, ledger, delete_if_unchanged

    async def get(self, library_id, document_id, **kwargs):
        self.ledger.library(library_id)
        known = self.ledger._state["documents"].get(document_id)
        if known is None or known["libraryId"] != library_id:
            return None
        row = await self.store.get(library_id, document_id, **kwargs)
        if row:
            require(known["status"] == "confirmed", "unconfirmed document exists; never adopt it")
            require(not known.get("deleted"), "deleted document reappeared")
            require(self.ledger.record_matches("document:" + document_id, row), "document body differs")
            require(row["id"] == document_id and row["libraryId"] == library_id, "document scope differs")
        return row

    async def create(self, record):
        library, document = record["libraryId"], identifier(record["id"])
        self.ledger.reserve_document(library, document, record["generation"])
        known = self.ledger.document(library, document, writing=True)
        require(known["status"] == "reserved" and not known.get("createAttempted"), "document create is single-attempt")
        require(await self.store.get(library, document) is None, "document already exists; never adopt it")
        self.ledger._change(
            "document-create-attempt",
            {"documentId": document},
            lambda s: s["documents"][document].update(createAttempted=True),
        )
        operation = self.ledger.begin_record("document-create", "document:" + document, record)
        try:
            result = await self.store.create(record)
            self.ledger.validate_record_response(operation, result)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        self.ledger._change(
            "confirm-document", {"documentId": document}, lambda s: s["documents"][document].update(status="confirmed")
        )
        return result

    async def replace(self, record):
        library, document = record["libraryId"], record["id"]
        known = self.ledger.document(library, document, writing=True)
        require(known["status"] == "confirmed", "unconfirmed document cannot be replaced")
        self.ledger.reserve_document(library, document, record["generation"])
        operation = self.ledger.begin_record("document-replace", "document:" + document, record)
        try:
            result = await self.store.replace(record)
            self.ledger.validate_record_response(operation, result)
        except Conflict:
            self.ledger.finish(operation, status="rejected")
            raise
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        return result

    def _check_rows(self, library, rows):
        for row in rows:
            self.ledger.document(library, row["id"])
            require(row.get("libraryId", library) == library, "document listing escaped scope")
        return rows

    async def list(self, library_id, *, status=None, facet=None):
        self.ledger.library(library_id)
        return self._check_rows(library_id, await self.store.list(library_id, status=status, facet=facet))

    async def discover(self, library_id, query, cost):
        self.ledger.library(library_id)
        return self._check_rows(library_id, await self.store.discover(library_id, query, cost))

    async def delete(self, library_id, document_id):
        self.ledger.document(library_id, document_id)
        key = "document:" + document_id
        self.ledger.key_settled(key)
        record = await self.get(library_id, document_id)
        if record is None:
            return
        require(self.ledger.record_matches(key, record, latest=True), "latest document body differs")
        require(record.get("status") == "deleting", "document is not fenced for deletion")
        require(not self.ledger.live or record.get("_etag"), "live document ETag missing")
        operation = self.ledger.begin("document-delete", key, etag=record.get("_etag"))
        try:
            if self.delete_if_unchanged:
                await self.delete_if_unchanged(record)
            else:
                await self.store.delete(library_id, document_id)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        self.ledger._change(
            "document-deleted", {"documentId": document_id}, lambda s: s["documents"][document_id].update(deleted=True)
        )


class GuardedBlobs:
    def __init__(self, store, ledger, *, snapshot_blob=None, delete_if_unchanged=None):
        require(
            not ledger.live or (snapshot_blob and delete_if_unchanged),
            "live blob deletion requires snapshot/ETag callbacks",
        )
        self.store, self.ledger = store, ledger
        self.snapshot_blob, self.delete_if_unchanged = snapshot_blob, delete_if_unchanged

    def _scope(self, path, *, writing=False, prefix=False):
        require(isinstance(path, str) and "\\" not in path, "invalid Blob path")
        require(str(PurePosixPath(path)) == path.rstrip("/"), "noncanonical Blob path")
        parts = path.rstrip("/").split("/")
        require(not any(part in {"", ".", ".."} for part in parts), "ambiguous Blob path component")
        self.ledger.library(parts[0], writing=writing)
        if prefix:
            require(path.endswith("/"), "Blob prefix needs delimiter")
            if len(parts) >= 2:
                self.ledger.document(parts[0], parts[1])
            return parts
        require(
            len(parts) >= 5 and parts[2] in {"original", "text", "images", "attempts"}, "unsupported Blob artifact path"
        )
        generation = parts[3].split("-", 1)[0] if parts[2] == "attempts" else parts[3]
        require(generation.isdigit() and int(generation) > 0, "Blob generation missing")
        if writing and parts[2] == "original":
            self.ledger.reserve_document(parts[0], parts[1], int(generation))
        self.ledger.document(parts[0], parts[1], writing=writing)
        require([parts[1], int(generation)] in self.ledger._state["generations"], "unowned Blob generation")
        return parts

    async def download(self, path):
        self._scope(path)
        data = await self.store.download(path)
        known = self.ledger._state["blobs"].get(path)
        require(known is not None, "unrecorded Blob artifact exists")
        require(hashlib.sha256(data).hexdigest() == known["sha256"], "owned Blob bytes changed")
        return data

    async def create(self, path, data, content_type):
        parts = self._scope(path, writing=True)
        require(parts[2] != "original" or len(data) <= LIMITS["sourceBytes"], "source byte ceiling")
        key, content_hash = "blob:" + path, hashlib.sha256(data).hexdigest()
        self.ledger.key_settled(key)
        known = self.ledger._state["blobs"].get(path)
        if known:
            require(not known.get("deleted") and known["sha256"] == content_hash, "immutable Blob write differs")
        else:
            try:
                await self.store.download(path)
            except (KeyError, ResourceNotFoundError):
                pass
            else:
                raise OwnershipError("Blob already exists; never adopt it")
            self.ledger._change(
                "reserve-blob",
                {"path": path, "initialReadAbsent": True},
                lambda s: s["blobs"].update({path: {"sha256": content_hash, "bytes": len(data), "deleted": False}}),
            )
        operation = self.ledger.begin("blob-create", key, sha256=content_hash, bytes=len(data))
        try:
            created = await self.store.create(path, data, content_type)
            if not created:
                require(known is not None, "Blob create raced with unknown writer")
                require(await self.store.download(path) == data, "existing immutable Blob differs")
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        return created

    async def upload(self, path, data, content_type):
        # Every qualification original/checkpoint path is new or byte-identical.
        # Never let the ordinary overwrite method adopt an unexpected Blob.
        await self.create(path, data, content_type)

    async def read_link(self, path):
        await self.download(path)
        return await self.store.read_link(path)

    async def list_prefix(self, prefix):
        self._scope(prefix, prefix=True)
        paths = await self.store.list_prefix(prefix)
        require(len(paths) == len(set(paths)), "duplicate Blob inventory")
        require(
            all(path.startswith(prefix) and path in self.ledger._state["blobs"] for path in paths),
            "unknown Blob in owned prefix",
        )
        return paths

    async def deletion_snapshot(self, path):
        parts = self._scope(path)
        require(path in self.ledger._state["blobs"], "unowned Blob delete")
        self.ledger.key_settled("library:" + parts[0])
        self.ledger.key_settled("document:" + parts[1])
        self.ledger.key_settled("blob:" + path)
        try:
            if self.snapshot_blob:
                observed = await self.snapshot_blob(path)
            else:
                data = await self.store.download(path)
                observed = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "etag": None}
        except (KeyError, ResourceNotFoundError):
            return None
        if observed is None:
            return None
        expected = self.ledger._state["blobs"][path]
        require(not expected.get("deleted"), "deleted Blob reappeared")
        require(
            observed["sha256"] == expected["sha256"] and observed["bytes"] == expected["bytes"],
            "Blob delete snapshot differs",
        )
        require(not self.ledger.live or observed.get("etag"), "live Blob ETag missing")
        return observed

    async def _delete_snapshot(self, path, observed):
        if observed is None:
            return
        operation = self.ledger.begin("blob-delete", "blob:" + path, snapshot=observed)
        try:
            if self.delete_if_unchanged:
                await self.delete_if_unchanged(path, observed)
            else:
                await self.store.delete(path)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        self.ledger._change("blob-deleted", {"path": path}, lambda s: s["blobs"][path].update(deleted=True))

    async def delete(self, path):
        await self._delete_snapshot(path, await self.deletion_snapshot(path))

    async def delete_prefix(self, prefix):
        # First inventory AND validate every exact object. No prefix deletion API.
        paths = await self.list_prefix(prefix)
        snapshots = [(path, await self.deletion_snapshot(path)) for path in paths]
        for path, snapshot in snapshots:
            await self._delete_snapshot(path, snapshot)
        return sum(snapshot is not None for _, snapshot in snapshots)


class GuardedChunks:
    def __init__(self, store, ledger, *, read_chunk=None, delete_if_unchanged=None, list_library_chunks=None):
        require(
            not ledger.live or (read_chunk and delete_if_unchanged), "live chunks require exact read/ETag callbacks"
        )
        self.store, self.ledger, self.read_chunk, self.delete_if_unchanged = (
            store,
            ledger,
            read_chunk,
            delete_if_unchanged,
        )
        self.list_library_chunks = list_library_chunks

    def _known(self, library, document, item):
        self.ledger.document(library, document)
        value = self.ledger._state["chunks"].get(item)
        require(value and value["libraryId"] == library and value["documentId"] == document, "unowned chunk")
        return value

    async def _read(self, library, document, item):
        if self.read_chunk:
            return await self.read_chunk(library, document, item)
        return self.store.items.get((library, document, item))

    async def upsert(self, row):
        library, document, item = row["libraryId"], row["documentId"], row["id"]
        self.ledger.document(library, document, writing=True)
        require([document, row["generation"]] in self.ledger._state["generations"], "unowned chunk generation")
        require(item == chunk_id(document, row["generation"], row["chunkIndex"]), "chunk ID differs from scope")
        require(
            isinstance(row.get("contentHash"), str) and len(row["contentHash"]) == 64, "immutable chunk hash required"
        )
        require(
            all(key in row for key in CHUNK_HASH_FIELDS)
            and digest({key: row[key] for key in CHUNK_HASH_FIELDS}) == row["contentHash"],
            "chunk canonical content hash differs",
        )
        key = "chunk:" + item
        self.ledger.key_settled(key)
        known = self.ledger._state["chunks"].get(item)
        if known:
            require(known["contentHash"] == row["contentHash"] and not known.get("deleted"), "immutable chunk differs")
            require(await self.deletion_snapshot(library, document, item) is not None, "owned chunk disappeared")
        else:
            require(await self._read(library, document, item) is None, "chunk already exists; never adopt it")

            def reserve(state):
                require(len(state["chunks"]) < LIMITS["chunks"], "chunk ceiling")
                state["chunks"][item] = {
                    **{k: row[k] for k in ("libraryId", "documentId", "generation", "contentHash")},
                    "deleted": False,
                }

            self.ledger._change("reserve-chunk", {"id": item, "initialPointReadAbsent": True}, reserve)
        operation = self.ledger.begin("chunk-create", key, contentHash=row["contentHash"])
        try:
            charge = await self.store.upsert(row)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation, requestCharge=charge)
        return charge

    async def generations(self, library_id, document_id):
        self.ledger.document(library_id, document_id)
        rows = await self.store.generations(library_id, document_id)
        require(len(rows) == len({item for item, _ in rows}), "duplicate chunk inventory")
        for item, generation in rows:
            require(self._known(library_id, document_id, item)["generation"] == generation, "chunk generation differs")
        return rows

    async def deletion_snapshot(self, library_id, document_id, item):
        known = self._known(library_id, document_id, item)
        self.ledger.key_settled("library:" + library_id)
        self.ledger.key_settled("document:" + document_id)
        self.ledger.key_settled("chunk:" + item)
        row = await self._read(library_id, document_id, item)
        if row is None:
            return
        require(not known.get("deleted"), "deleted chunk reappeared")
        require(
            row["id"] == item
            and row["libraryId"] == library_id
            and row["documentId"] == document_id
            and row["generation"] == known["generation"]
            and row.get("contentHash") == known["contentHash"]
            and all(key in row for key in CHUNK_HASH_FIELDS)
            and digest({key: row[key] for key in CHUNK_HASH_FIELDS}) == known["contentHash"],
            "chunk delete snapshot differs",
        )
        require(not self.ledger.live or row.get("_etag"), "live chunk ETag missing")
        return copy.deepcopy(row)

    async def delete(self, library_id, document_id, item):
        row = await self.deletion_snapshot(library_id, document_id, item)
        if row is None:
            return
        operation = self.ledger.begin("chunk-delete", "chunk:" + item, etag=row.get("_etag"))
        try:
            if self.delete_if_unchanged:
                await self.delete_if_unchanged(row)
            else:
                await self.store.delete(library_id, document_id, item)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation)
        self.ledger._change("chunk-deleted", {"id": item}, lambda s: s["chunks"][item].update(deleted=True))

    async def patch(self, library_id, document_id, item, fields):
        self._known(library_id, document_id, item)
        require(set(fields) <= {"facets", "author", "periodStart", "periodEnd"}, "chunk patch exceeds metadata scope")
        operation = self.ledger.begin("chunk-patch", "chunk:" + item, fieldsSha256=digest(fields))
        try:
            charge = await self.store.patch(library_id, document_id, item, fields)
        except BaseException:
            self.ledger.finish(operation, status="unknown")
            raise
        self.ledger.finish(operation, requestCharge=charge)
        return charge

    async def opening(self, library_id, document_id, generation):
        self.ledger.document(library_id, document_id)
        require([document_id, generation] in self.ledger._state["generations"], "unowned opening generation")
        return await self.store.opening(library_id, document_id, generation)


async def guard_owned_inventory(libraries, documents, chunks, blobs, ledger):
    """Validate the complete owned scope before cleanup's first destructive call.

    This is private evidence, not a public receipt. Scoped server inventories catch
    unexpected rows even under an unrecorded document. Every owned object is then
    read by exact key to record current ETag/hash; each delete revalidates it again.
    This does not prove absence after deletion, which needs an independent client.
    """
    ledger.assert_settled()
    require(not any(row["status"] == "running" for row in ledger._state["queue"]), "queue writer still running")
    require(not ledger.live or chunks.list_library_chunks is not None, "live cleanup needs library chunk inventory")
    result = {"libraries": {}, "documents": {}, "chunks": {}, "blobs": {}}
    for library in ledger._state["libraries"]:
        result["libraries"][library] = await libraries.get(library)
        if result["libraries"][library] is not None:
            require(
                ledger.record_matches("library:" + library, result["libraries"][library], latest=True),
                "latest library body differs",
            )
        await documents.list(library)
        if chunks.list_library_chunks:
            rows = await chunks.list_library_chunks(library)
        else:
            rows = [row for (lib, _, _), row in chunks.store.items.items() if lib == library]
        seen = set()
        for row in rows:
            require(row.get("libraryId") == library, "library chunk inventory escaped scope")
            require(row["id"] not in seen, "duplicate library chunk inventory")
            seen.add(row["id"])
            chunks._known(library, row["documentId"], row["id"])
        await blobs.list_prefix(library + "/")
    for document, known in ledger._state["documents"].items():
        library = known["libraryId"]
        result["documents"][document] = await documents.get(library, document)
        if result["documents"][document] is not None:
            require(
                ledger.record_matches("document:" + document, result["documents"][document], latest=True),
                "latest document body differs",
            )
        await chunks.generations(library, document)
    for item, known in ledger._state["chunks"].items():
        result["chunks"][item] = await chunks.deletion_snapshot(known["libraryId"], known["documentId"], item)
    for path in ledger._state["blobs"]:
        result["blobs"][path] = await blobs.deletion_snapshot(path)
    return result


class LedgerQueue:
    """Durable private queue; processing is explicit and never receives from Azure."""

    def __init__(self, ledger):
        self.ledger = ledger

    async def send(self, message, *, session_id):
        self.ledger.library(session_id, writing=True)
        require(message.get("libraryId") == session_id, "message escaped owned library session")
        require(
            message.get("type")
            in {
                "ingest",
                "enrichImages",
                "purgeDocument",
                "purgeLibrary",
                "retag",
                "proposeDescription",
                "reconcileLibrary",
            },
            "unknown local queue operation",
        )
        if document := message.get("documentId"):
            self.ledger.document(session_id, document)
        require(
            message["type"] not in {"ingest", "enrichImages", "purgeDocument", "retag"} or document,
            "document-scoped message needs document ID",
        )
        if "generation" in message:
            require(
                [document, message["generation"]] in self.ledger._state["generations"], "unowned message generation"
            )

        def change(state):
            require(len(state["queue"]) < 200, "message ceiling")
            state["queue"].append(
                {
                    "id": uuid.uuid4().hex,
                    "sessionId": session_id,
                    "message": copy.deepcopy(message),
                    "status": "pending",
                }
            )

        self.ledger._change("queue-send", {"messageSha256": digest(message)}, change)

    @property
    def pending(self):
        return [copy.deepcopy(row) for row in self.ledger._state["queue"] if row["status"] == "pending"]

    async def drain(self, worker, *, max_messages=100):
        require(
            not any(row["status"] in {"running", "failed"} for row in self.ledger._state["queue"]),
            "prior queue attempt needs explicit review",
        )
        count = 0
        while self.pending:
            require(count < max_messages, "drain message ceiling")
            entry = self.pending[0]

            def mark(status, entry=entry):
                self.ledger._change(
                    "queue-" + status,
                    {"messageId": entry["id"]},
                    lambda s: next(row for row in s["queue"] if row["id"] == entry["id"]).update(status=status),
                )

            mark("running")
            try:
                await worker.handle(copy.deepcopy(entry["message"]))
            except BaseException:
                mark("failed")
                raise
            mark("completed")
            count += 1
        return count

    def cancel_pending(self, reason):
        require(isinstance(reason, str) and reason.strip(), "cancellation reason required")
        self.ledger.assert_settled()
        require(
            not any(row["status"] == "running" for row in self.ledger._state["queue"]), "queue writer still running"
        )

        def cancel(state):
            for row in state["queue"]:
                if row["status"] in {"pending", "failed"}:
                    row.update(status="cancelled", reason=reason)

        self.ledger._change("queue-cancel-pending", {"reason": reason}, cancel)
