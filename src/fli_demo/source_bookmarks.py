"""App-owned, sealed source-selection metadata; never a source or authority cache."""

from __future__ import annotations

import copy
import hashlib
import hmac
import math
import secrets
import time

from fli_demo.navigation_refs import PIN, ReferenceError, canonical, validate_reference

SCHEMA = "fli.source-bookmarks.v1"
TOKEN_PREFIX = "sbm"
INSTRUCTIONS = (
    "Source bookmarks are app-owned records of complete source selections previously delivered to this chat. "
    'Copy sourceBookmark.read exactly, for example {"bookmark":"<returned bookmark token>"}; replace the '
    "placeholder and omit reference/references/mode/documents/paging. A bookmark survives turns only while "
    "this conversation's protected app state remains valid; ordinary actions still expire each turn. "
    "Labels and citations in the saved-bookmark list are metadata, not fresh evidence. Read the bookmark "
    "before answering. Every use checks the original source snapshot, scope and current access again. "
    "A changed/deleted/inaccessible source or expired bookmark is an explicit failure, never permission to "
    "drop pins, rebind to a newer version or answer from prior prose. Rediscover explicitly when needed. "
    "Do not expose bookmark tokens or internal IDs in human answers."
)


class SourceBookmarkError(ReferenceError):
    def __init__(self, message, *, code="source_bookmark_unavailable"):
        super().__init__(message, code=code)

    def payload(self):
        return {
            "error": self.code,
            "errorCategory": self.code,
            "complete": False,
            "reason": str(self),
            "nextAction": "The bookmark did not supply source evidence. Use the current authorized catalogue "
            "and pinned outline to choose the intended source explicitly; never edit a bookmark or remove pins.",
        }


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _identity(library_id, request, source_sha):
    # Transport budgets may differ between reads. Reuse the original binding,
    # including its original controls, when source snapshot/scope are identical.
    scope = {k: v for k, v in request.items() if k not in {"includeText", "maxTokens"}}
    return _digest({"libraryId": library_id, "request": scope, "sourceSha256": source_sha})


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _key(value):
    if not isinstance(value, bytes) or len(value) < 32:
        raise SourceBookmarkError("A separate trusted application authority key is required")
    return value


def _binding(conversation_id, actor_id, target):
    values = {"conversationId": conversation_id, "actorId": actor_id, "target": target}
    if any(not isinstance(v, str) or not v for v in values.values()):
        raise SourceBookmarkError("A complete application conversation binding is required")
    return values


def _record_valid(record):
    fields = {"token", "identity", "libraryId", "request", "sourceSha256", "fileName", "label", "citations", "scope"}
    if not isinstance(record, dict) or set(record) != fields:
        raise SourceBookmarkError("Invalid bookmark checkpoint entry")
    token = record["token"]
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) != len(TOKEN_PREFIX) + 24:
        raise SourceBookmarkError("Invalid bookmark token")
    if any(c not in "0123456789abcdef" for c in token[len(TOKEN_PREFIX) :]):
        raise SourceBookmarkError("Invalid bookmark token")
    request = record["request"]
    validate_reference("read", request)
    if request["mode"] not in {"full", "selected"} or len(request["documents"]) != 1:
        raise SourceBookmarkError("Only complete single-document source selections can be bookmarked")
    if request.get("includeText", True) is not True or not _sha(record["sourceSha256"]):
        raise SourceBookmarkError("Bookmark source binding is unavailable")
    if any(not isinstance(record[k], str) or not record[k] for k in ("libraryId", "fileName", "label")):
        raise SourceBookmarkError("Bookmark labels are unavailable")
    if (
        not isinstance(record["citations"], list)
        or not 1 <= len(record["citations"]) <= 2
        or any(not isinstance(c, str) or not c for c in record["citations"])
    ):
        raise SourceBookmarkError("Bookmark citations are unavailable")
    scope = record["scope"]
    if not isinstance(scope, dict) or set(scope) != {"mode", "sourceAtomCount", "citationCoverage", "description"}:
        raise SourceBookmarkError("Invalid bookmark source scope")
    if scope["mode"] != request["mode"] or type(scope["sourceAtomCount"]) is not int or scope["sourceAtomCount"] < 1:
        raise SourceBookmarkError("Invalid bookmark source scope")
    if scope["citationCoverage"] not in {"single_source_atom", "first_and_last_source_atoms"}:
        raise SourceBookmarkError("Invalid bookmark citation coverage")
    if not isinstance(scope["description"], str) or not scope["description"]:
        raise SourceBookmarkError("Bookmark scope description is unavailable")
    identity = _identity(record["libraryId"], record["request"], record["sourceSha256"])
    if record["identity"] != identity:
        raise SourceBookmarkError("Bookmark binding differs")


class SourceBookmarkStore:
    """Trusted session owner. Serialized state alone cannot recreate this owner.

    ``close`` clears this in-memory instance. A prior signed checkpoint remains
    restorable only with the separately retained key and matching context, until
    its absolute expiry. Logout/reset must discard that key and checkpoint.
    """

    def __init__(
        self, conversation_id, actor_id, target, *, authority_key, ttl_seconds=86400, max_entries=32, clock=time.time
    ):
        self.binding = _binding(conversation_id, actor_id, target)
        self._authority = _key(authority_key)
        if type(ttl_seconds) not in (int, float) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise SourceBookmarkError("Bookmark lifetime must be positive")
        if type(max_entries) is not int or not 1 <= max_entries <= 32:
            raise SourceBookmarkError("Bookmark capacity must be between 1 and 32")
        self.clock, self.max_entries = clock, max_entries
        self.created_at, self.closed = float(clock()), False
        self.expires_at = self.created_at + ttl_seconds
        self._entries = {}

    @property
    def expired(self):
        return self.clock() >= self.expires_at

    def check_context(self, conversation_id, actor_id, target):
        if self.closed or self.expired or self.binding != _binding(conversation_id, actor_id, target):
            raise SourceBookmarkError("Bookmark owner is expired, closed or belongs to another conversation")

    def checkpoint(self):
        self.check_context(self.binding["conversationId"], self.binding["actorId"], self.binding["target"])
        payload = {
            "binding": copy.deepcopy(self.binding),
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "maxEntries": self.max_entries,
            "entries": copy.deepcopy(list(self._entries.values())),
        }
        signed = {"schema": SCHEMA, "payload": payload}
        return {
            **signed,
            "signature": hmac.new(self._authority, canonical(signed).encode(), hashlib.sha256).hexdigest(),
        }

    @classmethod
    def restore(cls, checkpoint, *, authority_key, conversation_id, actor_id, target, clock=time.time):
        key = _key(authority_key)
        try:
            if not isinstance(checkpoint, dict) or set(checkpoint) != {"schema", "payload", "signature"}:
                raise ValueError()
            signed = {"schema": checkpoint["schema"], "payload": checkpoint["payload"]}
            expected = hmac.new(key, canonical(signed).encode(), hashlib.sha256).hexdigest()
            if (
                checkpoint["schema"] != SCHEMA
                or not isinstance(checkpoint["signature"], str)
                or not hmac.compare_digest(expected, checkpoint["signature"])
            ):
                raise ValueError()
            payload = copy.deepcopy(checkpoint["payload"])
            if set(payload) != {"binding", "createdAt", "expiresAt", "maxEntries", "entries"}:
                raise ValueError()
            binding = _binding(conversation_id, actor_id, target)
            if payload["binding"] != binding:
                raise ValueError()
            if any(
                type(payload[k]) not in (int, float) or not math.isfinite(payload[k])
                for k in ("createdAt", "expiresAt")
            ):
                raise ValueError()
            if not payload["createdAt"] <= clock() < payload["expiresAt"]:
                raise ValueError()
            capacity = payload["maxEntries"]
            entries = payload["entries"]
            if (
                type(capacity) is not int
                or not 1 <= capacity <= 32
                or not isinstance(entries, list)
                or len(entries) > capacity
            ):
                raise ValueError()
            for entry in entries:
                _record_valid(entry)
            if len({e["token"] for e in entries}) != len(entries) or len({e["identity"] for e in entries}) != len(
                entries
            ):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise SourceBookmarkError("Protected bookmark checkpoint is unavailable, changed or expired") from None
        owner = cls.__new__(cls)
        owner.binding, owner._authority, owner.clock = binding, key, clock
        owner.created_at, owner.expires_at = payload["createdAt"], payload["expiresAt"]
        owner.max_entries, owner.closed = capacity, False
        owner._entries = {e["token"]: e for e in entries}
        return owner

    def prepare(self, library_id, request, payload):
        """Extract metadata only; the caller commits after successful delivery."""
        if self.closed or self.expired:
            return None, "bookmark_owner_expired"
        documents = payload.get("documents")
        if payload.get("mode") not in {"selected", "full"} or payload.get("selectionComplete") is not True:
            return None, None
        if (
            not isinstance(documents, list)
            or len(documents) != 1
            or not isinstance(request, dict)
            or len(request.get("documents", [])) != 1
        ):
            return None, "multi_document_bookmarks_unavailable"
        document = documents[0]
        rows = document.get("sections")
        if (
            not rows
            or not isinstance(rows, list)
            or any(
                not isinstance(row.get("text"), str)
                or row.get("textSource", "extracted_source_text") != "extracted_source_text"
                for row in rows
            )
        ):
            return None, "original_source_selection_unavailable"
        request = {k: copy.deepcopy(v) for k, v in request.items() if k != "details"}
        if request.get("mode") != payload["mode"] or any(
            request["documents"][0].get(k) != document.get(k) for k in PIN
        ):
            return None, "exact_source_binding_unavailable"
        if (
            not _sha(document.get("sourceSha256"))
            or not document.get("fileName")
            or any(not row.get("citation") for row in rows)
        ):
            return None, "exact_source_binding_unavailable"
        citations = list(dict.fromkeys([rows[0]["citation"], rows[-1]["citation"]]))
        nodes = (document.get("selectionSummary") or {}).get("nodes") or []
        first = nodes[0] if nodes else {}
        description = "entire document" if request["mode"] == "full" else "selected original source atoms"
        if first:
            description = str(first.get("kind", "source")) + ": " + str(first.get("title", "selected source"))
            if first.get("readScope"):
                description += "; " + first["readScope"].replace("_", " ")
            if len(nodes) > 1:
                description += f"; plus {len(nodes) - 1} other selected nodes"
        scope = {
            "mode": request["mode"],
            "sourceAtomCount": len(rows),
            "citationCoverage": "single_source_atom" if len(rows) == 1 else "first_and_last_source_atoms",
            "description": description,
        }
        path = [entry["title"] for entry in first.get("authoredPath", []) if isinstance(entry.get("title"), str)]
        heading = " / ".join(path) or rows[0].get("heading")
        label = document["fileName"] + (" — " + heading if isinstance(heading, str) and heading else "")
        label += f" — {request['mode']} selection ({len(rows)} source atoms)"
        identity = _identity(library_id, request, document["sourceSha256"])
        existing = next((e for e in self._entries.values() if e["identity"] == identity), None)
        if existing:
            return copy.deepcopy(existing), None
        if len(self._entries) >= self.max_entries:
            return None, "conversation_bookmark_limit_reached"
        record = {
            "token": TOKEN_PREFIX + secrets.token_hex(12),
            "identity": identity,
            "libraryId": library_id,
            "request": request,
            "sourceSha256": document["sourceSha256"],
            "fileName": document["fileName"],
            "label": label,
            "citations": citations,
            "scope": scope,
        }
        _record_valid(record)
        return record, None

    def commit(self, records):
        if self.closed or self.expired:
            return
        for record in records:
            _record_valid(record)
            if record["token"] in self._entries:
                if self._entries[record["token"]] != record:
                    raise SourceBookmarkError("A bookmark cannot be rebound")
            elif len(self._entries) < self.max_entries:
                if any(e["identity"] == record["identity"] for e in self._entries.values()):
                    raise SourceBookmarkError("Duplicate bookmark selection cannot receive a second token")
                self._entries[record["token"]] = copy.deepcopy(record)
            else:
                raise SourceBookmarkError("Bookmark capacity was exceeded; no token may be silently discarded")

    @property
    def available_slots(self):
        return self.max_entries - len(self._entries)

    def is_saved(self, token):
        return token in self._entries

    @staticmethod
    def visible(record):
        return {
            "read": {"bookmark": record["token"]},
            **{k: copy.deepcopy(record[k]) for k in ("fileName", "label", "citations", "scope")},
        }

    def catalogue(self, library_ids, *, read_tools=None):
        if self.closed or self.expired:
            return []
        return [
            {
                **self.visible(record),
                **(
                    {"tool": read_tools[record["libraryId"]]}
                    if read_tools is not None
                    else {"libraryId": record["libraryId"]}
                ),
            }
            for record in self._entries.values()
            if record["libraryId"] in library_ids and (read_tools is None or record["libraryId"] in read_tools)
        ]

    def resolve(self, token, *, conversation_id, actor_id, target, library_id, library_ids):
        self.check_context(conversation_id, actor_id, target)
        record = self._entries.get(token) if isinstance(token, str) else None
        if record is None or record["libraryId"] != library_id or library_id not in library_ids:
            raise SourceBookmarkError("Unknown, detached or foreign source bookmark")
        return copy.deepcopy(record)

    @staticmethod
    def verify_source(record, payload):
        # Refusals/inspections have no fresh body; preserve their original budget
        # and coverage explanation instead of turning them into source evidence.
        documents = payload.get("documents") or []
        if any(
            document.get("sourceSha256") is not None and document["sourceSha256"] != record["sourceSha256"]
            for document in documents
        ):
            raise SourceBookmarkError(
                "The recorded source hash changed; no bookmark evidence was delivered", code="document_changed"
            )
        if not any(document.get("sections") for document in documents):
            return
        if (
            len(documents) != 1
            or any(documents[0].get(k) != record["request"]["documents"][0][k] for k in PIN)
            or documents[0].get("sourceSha256") != record["sourceSha256"]
        ):
            raise SourceBookmarkError(
                "The recorded source snapshot changed; no bookmark source was delivered", code="document_changed"
            )
        if payload.get("mode") != record["request"]["mode"]:
            raise SourceBookmarkError("The bookmark source scope differs", code="source_scope_unavailable")

    def close(self):
        self.closed = True
        self._entries.clear()
        self._authority = b""
