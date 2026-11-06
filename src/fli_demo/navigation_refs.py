"""Application-held, exact navigation references for one agent turn.

References contain no authority and never cache source bodies. Resolution only
recovers an already returned, pinned request; the normal API still checks access,
source integrity, publication and deletion on every use. No durable restore API
exists: a new turn gets a new namespace and must reacquire expired references.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets

from fli.selector_hints import GUIDANCE

CONTRACT = "fli.navigation.v1"
PIN = ("documentId", "generation", "contentRevision")
READ_KEYS = {"mode", "documents", "includeText", "maxTokens", "afterSection", "sectionLimit"}
SELECTOR_KEYS = {*PIN, "nodeIds", "nodeScope", "outlineNodeId", "sectionIds", "evidenceIds", "paragraphs"}
TRANSPORT = {"includeText", "maxTokens", "details"}
REFERENCE_PATTERN = r"^nav[A-Za-z0-9]+-[1-9][0-9]*$"
SOURCE_IDS = {"nodeIds", "sectionIds", "evidenceIds", "outlineNodeId"}


def _closed_alternative(fields, required):
    # Field types are defined once on the enclosing object. These intersecting
    # closed alternatives enforce exclusivity without duplicating that schema.
    return {"properties": {key: {} for key in sorted(fields)}, "required": required, "additionalProperties": False}


def _controls(arguments):
    if any(key in arguments and type(arguments[key]) is not bool for key in ("includeText", "details")):
        raise ReferenceError("includeText and details must be booleans", code="invalid_request")
    if "maxTokens" in arguments and (
        type(arguments["maxTokens"]) is not int or not 1 <= arguments["maxTokens"] <= 200000
    ):
        raise ReferenceError("maxTokens must be an integer from 1 to 200000", code="invalid_request")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def target_of(index):
    """Bind the registry to this client's API target, without adding it to model context."""
    return str(getattr(getattr(index, "http", None), "base_url", "in-process:" + str(id(index))))


class ReferenceError(ValueError):
    """Content-free error: never echo a rejected reference or source selector."""

    def __init__(self, message, *, code="navigation_reference_unavailable"):
        super().__init__(message)
        self.code = code

    def payload(self):
        return {
            "error": self.code,
            "errorCategory": self.code,
            "complete": False,
            "reason": str(self),
            "nextAction": 'Read an actions value with {"reference":"<copy returned actions value>"}; '
            "replace the placeholder, omit mode/documents/paging, and never use documentRef as a read action. "
            "If no current action remains, rediscover the file and copy its returned pinned outlineSelector. "
            "Do not remove snapshot pins or substitute another source.",
        }


def _pin(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("documentId"), str)
        or not value["documentId"].strip()
        or type(value.get("generation")) is not int
        or value["generation"] < 1
        or type(value.get("contentRevision")) is not int
        or value["contentRevision"] < 0
    ):
        raise ReferenceError("An exact source snapshot is required")


def validate_reference(kind, value):
    if kind == "document":
        _pin(value)
        if set(value) != set(PIN):
            raise ReferenceError("Invalid document reference")
    elif kind == "read":
        if (
            not isinstance(value, dict)
            or set(value) - READ_KEYS
            or value.get("mode") not in {"outline", "selected", "full"}
        ):
            raise ReferenceError("Invalid read reference")
        docs = value.get("documents")
        if not isinstance(docs, list) or not 1 <= len(docs) <= 20:
            raise ReferenceError("Invalid source selection")
        for doc in docs:
            _pin(doc)
            if set(doc) - SELECTOR_KEYS:
                raise ReferenceError("Unsupported source selection")
    else:
        raise ReferenceError("Unsupported navigation reference kind")
    canonical(value)  # no executable/custom objects in the registry


def combine(selectors):
    """Union compatible exact reads; never turn paragraph membership into cell coverage."""
    if len(selectors) == 1:
        return copy.deepcopy(selectors[0])
    mode = selectors[0]["mode"]
    if mode not in {"selected", "full"}:
        raise ReferenceError("Only compatible source reads can be combined")
    documents, by_id = [], {}
    for selection in selectors:
        if selection["mode"] != mode or set(selection) - {"mode", "documents", "includeText", "maxTokens"}:
            raise ReferenceError("Incompatible reference scopes")
        # Transport directives on registered actions cannot be silently erased.
        if any(selection.get(k) != selectors[0].get(k) for k in ("includeText", "maxTokens")):
            raise ReferenceError("Incompatible reference controls")
        for source in selection["documents"]:
            doc = copy.deepcopy(source)
            extra = set(doc) - set(PIN)
            fields = extra - {"nodeScope"}
            if mode == "full" and extra:
                raise ReferenceError("Invalid full scope")
            if mode == "selected" and (len(fields) != 1 or not fields <= {"nodeIds", "sectionIds", "evidenceIds"}):
                raise ReferenceError("Incompatible selected scope")
            previous = by_id.get(doc["documentId"])
            if previous is None:
                documents.append(doc)
                by_id[doc["documentId"]] = doc
            else:
                if any(previous.get(k) != doc.get(k) for k in (*PIN, "nodeScope")) or set(previous) != set(doc):
                    raise ReferenceError("Conflicting snapshot or scope")
                for field in fields:
                    previous[field] = list(dict.fromkeys([*previous[field], *doc[field]]))
                    if len(previous[field]) > 20:
                        raise ReferenceError("Combined selection exceeds the existing limit")
    if len(documents) > 20:
        raise ReferenceError("Combined selection exceeds the existing limit")
    return {**copy.deepcopy(selectors[0]), "documents": documents}


class NavigationSession:
    def __init__(self, agent_id, library_ids, target, *, namespace=None, bookmark_store=None, conversation_id=None):
        self.agent_id, self.library_ids, self.target = agent_id, frozenset(library_ids), target
        self.namespace = namespace or secrets.token_hex(6)
        if not isinstance(self.namespace, str) or not self.namespace.isalnum():
            raise ValueError("invalid navigation namespace")
        self.entries, self.keys, self.requests, self.resolutions = {}, {}, {}, []
        self.closed = False
        self.granular_libraries = set()
        self.tool_names, self.argument_keys = {}, {}
        self.bookmark_store, self.conversation_id = bookmark_store, conversation_id
        self.pending_bookmarks, self.bookmarks_committed = {}, 0
        if bookmark_store is not None:
            from fli_demo.source_bookmarks import SourceBookmarkStore

            if not isinstance(bookmark_store, SourceBookmarkStore):
                raise ValueError("Bookmarks require a trusted application owner, not transcript state")
            bookmark_store.check_context(conversation_id, agent_id, target)

    def _bound(self, library_id, agent_id, target):
        if self.closed or library_id not in self.library_ids or agent_id != self.agent_id or target != self.target:
            raise ReferenceError("Reference session is unavailable")

    def register(self, kind, library_id, value):
        self._bound(library_id, self.agent_id, self.target)
        validate_reference(kind, value)
        key = canonical([kind, library_id, value])
        if key not in self.keys:
            reference = f"nav{self.namespace}-{len(self.keys) + 1}"
            self.keys[key] = reference
            self.entries[reference] = {"kind": kind, "library": library_id, "value": copy.deepcopy(value)}
        return self.keys[key]

    def lookup(self, reference, kind, library_id, agent_id, target):
        self._bound(library_id, agent_id, target)
        entry = self.entries.get(reference) if isinstance(reference, str) else None
        if entry is None or entry["kind"] != kind or entry["library"] != library_id:
            raise ReferenceError("Unknown or expired navigation reference")
        return copy.deepcopy(entry["value"])

    def resolve_arguments(self, operation, library_id, agent_id, arguments, *, target=None):
        self._bound(library_id, agent_id, target if target is not None else self.target)
        if not isinstance(arguments, dict):
            raise ReferenceError("Tool arguments must be an object")
        result = copy.deepcopy(arguments)
        if "bookmark" in result:
            from fli_demo.source_bookmarks import SourceBookmarkError

            if operation != "read" or self.bookmark_store is None or set(result) - {"bookmark", *TRANSPORT}:
                raise SourceBookmarkError("Use bookmark alone with optional includeText/maxTokens/details")
            _controls(result)
            record = self.bookmark_store.resolve(
                result["bookmark"],
                conversation_id=self.conversation_id,
                actor_id=agent_id,
                target=self.target,
                library_id=library_id,
                library_ids=self.library_ids,
            )
            result = {**record["request"], **{k: v for k, v in result.items() if k in TRANSPORT}}
        elif operation == "read" and ("reference" in result or "references" in result):
            if set(result) - {"reference", "references", *TRANSPORT} or ("reference" in result) == (
                "references" in result
            ):
                raise ReferenceError("A reference cannot be combined with scope overrides")
            refs = [result["reference"]] if "reference" in result else result["references"]
            if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
                raise ReferenceError("One to twenty exact references required")
            _controls(result)
            resolved = combine([self.lookup(ref, "read", library_id, agent_id, self.target) for ref in refs])
            result = {**resolved, **{k: v for k, v in result.items() if k in TRANSPORT}}
        elif operation == "search" and "documentRef" in result:
            if "document" in result:
                raise ReferenceError("A document reference cannot override a selector")
            result["document"] = self.lookup(result.pop("documentRef"), "document", library_id, agent_id, self.target)
        elif any(key in result for key in ("reference", "references", "documentRef")):
            raise ReferenceError("Reference operation differs")
        if library_id in self.granular_libraries:
            if operation == "read":
                self._explicit_read(result)
            elif operation == "search":
                allowed = self.argument_keys.get((library_id, "search"))
                if allowed is not None and set(result) - allowed:
                    raise ReferenceError("Unsupported search arguments", code="invalid_request")
                if "document" in result:
                    validate_reference("document", result["document"])
                    if result["document"]["documentId"] in self.entries:
                        raise ReferenceError("documentId is a source identifier, not a navigation reference")
        if "details" in result and (operation != "read" or type(result["details"]) is not bool):
            raise ReferenceError("details must be a read boolean")
        return result

    def _explicit_read(self, arguments):
        from fli.api.reading import ReadBody

        _controls(arguments)
        request = {key: value for key, value in arguments.items() if key != "details"}
        try:
            validate_reference("read", request)
        except ReferenceError as error:
            raise ReferenceError(str(error), code="invalid_request") from None
        for document in request["documents"]:
            if document["documentId"] in self.entries:
                raise ReferenceError("documentId is a source identifier, not a navigation reference")
            if "nodeScope" in document and not document.get("nodeIds"):
                raise ReferenceError(
                    "nodeScope requires nodeIds; it cannot be silently ignored", code="invalid_request"
                )
            for field in SOURCE_IDS:
                values = document.get(field, [])
                values = [values] if isinstance(values, str) else values
                if (
                    self.bookmark_store is not None
                    and isinstance(values, list)
                    and any(isinstance(value, str) and re.fullmatch(r"sbm[0-9a-f]{24}", value) for value in values)
                ):
                    from fli_demo.source_bookmarks import SourceBookmarkError

                    raise SourceBookmarkError(
                        "Source bookmark tokens belong in top-level bookmark, never source locator fields"
                    )
                if isinstance(values, list) and any(
                    isinstance(value, str)
                    and (
                        re.fullmatch(REFERENCE_PATTERN, value)
                        or self.bookmark_store is not None
                        and re.fullmatch(r"sbm[0-9a-f]{24}", value)
                    )
                    for value in values
                ):
                    raise ReferenceError("Action references belong in top-level reference, never source locator fields")
        try:
            ReadBody.model_validate(request)
        except ValueError:
            raise ReferenceError(
                "Explicit reads require a supported mode and matching pinned source scope", code="invalid_request"
            ) from None

    def recovery_payload(self, payload, library_id, arguments, *, agent_id, target):
        """Offer an explicit next call, never execute, infer scope or grant access."""
        if library_id not in self.granular_libraries or payload.get("errorCategory") not in {
            "navigation_reference_unavailable",
            "invalid_request",
            "invalid_selector",
            "source_scope_unavailable",
            "source_bookmark_unavailable",
        }:
            return payload
        try:
            self._bound(library_id, agent_id, target)
        except ReferenceError:
            return payload
        names = self.tool_names.get(library_id, {})
        if "read" not in names or "discover" not in names:
            return payload
        recovery = {
            "discover": {"tool": names["discover"], "arguments": {}},
            "afterDiscovery": "Choose the intended file and copy its returned outlineSelector as the entire read "
            "arguments object, retaining documentId/generation/contentRevision. "
            "Then use actions.outline or actions.read "
            "in top-level reference. documentRef is only a top-level search argument, never a read action.",
        }
        arguments = arguments if isinstance(arguments, dict) else {}
        candidates = arguments.get("references", [arguments["reference"]] if "reference" in arguments else [])
        correctable = ("reference" in arguments) != ("references" in arguments) and bool(
            set(arguments) - {"reference", "references", *TRANSPORT}
        )
        if not candidates:
            candidates = []
            correctable = True
            for document in arguments.get("documents", []) if isinstance(arguments.get("documents"), list) else []:
                if isinstance(document, dict):
                    for key in SOURCE_IDS:
                        values = document.get(key, [])
                        values = [values] if isinstance(values, str) else values
                        if isinstance(values, list):
                            if any(
                                not isinstance(value, str) or not re.fullmatch(REFERENCE_PATTERN, value)
                                for value in values
                            ):
                                correctable = False
                            candidates.extend(
                                value
                                for value in values
                                if isinstance(value, str) and re.fullmatch(REFERENCE_PATTERN, value)
                            )
        try:
            _controls(arguments)
            if (
                not correctable
                or payload.get("errorCategory") == "source_scope_unavailable"
                or not isinstance(candidates, list)
                or not 1 <= len(candidates) <= 20
            ):
                raise ReferenceError("No exact read action available")
            selection = combine([self.lookup(ref, "read", library_id, agent_id, target) for ref in candidates])
            recovery["read"] = {
                "tool": names["read"],
                "arguments": {
                    **({"reference": candidates[0]} if len(candidates) == 1 else {"references": candidates}),
                    **{key: arguments[key] for key in TRANSPORT if key in arguments},
                },
                "scope": selection["mode"],
                "note": "Executable example for the original returned action only. It preserves its snapshot/scope; "
                "use it only if that action is intended. Conflicting mode/scope arguments were not applied. "
                "No read has been performed by this correction.",
            }
        except ReferenceError:
            pass
        if self.bookmark_store is not None and "bookmark" in arguments and set(arguments) - {"bookmark", *TRANSPORT}:
            try:
                _controls(arguments)
                if payload.get("errorCategory") == "source_scope_unavailable":
                    raise ReferenceError("The unchanged failed source scope must not be retried")
                record = self.bookmark_store.resolve(
                    arguments["bookmark"],
                    conversation_id=self.conversation_id,
                    actor_id=agent_id,
                    target=target,
                    library_id=library_id,
                    library_ids=self.library_ids,
                )
                recovery["read"] = {
                    "tool": names["read"],
                    "arguments": {
                        "bookmark": record["token"],
                        **{k: arguments[k] for k in TRANSPORT if k in arguments},
                    },
                    "scope": record["scope"],
                    "label": record["label"],
                    "note": "Executable example for this saved source selection only. Conflicting mode/scope "
                    "arguments were not applied and no read occurred. Use only if this recorded selection is intended.",
                }
            except ReferenceError:
                pass
        return {
            **payload,
            "nextAction": "The call was rejected. If recovery.read matches the intended returned action, copy its "
            "arguments exactly into its named tool. Do not add mode/documents/paging. Otherwise use recovery.discover "
            "and the returned pinned outline route. Do not guess IDs, drop pins or switch source versions silently.",
            "recovery": recovery,
            **(
                {
                    "nextAction": "The bookmark call was rejected. If recovery.read matches the saved selection, "
                    "copy its arguments exactly into its named tool; omit reference/references/mode/documents/paging. "
                    "Otherwise use recovery.discover and explicitly choose the current pinned source. "
                    "Never edit tokens or silently switch source versions."
                }
                if self.bookmark_store is not None and "bookmark" in arguments
                else {}
            ),
        }

    def bookmark_hints(self):
        if self.bookmark_store is None:
            return ""
        from fli_demo.source_bookmarks import INSTRUCTIONS

        read_tools = {lib: names["read"] for lib, names in self.tool_names.items() if "read" in names}
        hints = self.bookmark_store.catalogue(self.library_ids & self.granular_libraries, read_tools=read_tools)
        return (
            "\n" + INSTRUCTIONS + "\nSaved source selection metadata (not fresh source evidence): " + canonical(hints)
        )

    def verify_bookmark_source(self, arguments, payload, *, library_id, agent_id, target):
        if self.bookmark_store is None or not isinstance(arguments, dict) or "bookmark" not in arguments:
            return
        record = self.bookmark_store.resolve(
            arguments["bookmark"],
            conversation_id=self.conversation_id,
            actor_id=agent_id,
            target=target,
            library_id=library_id,
            library_ids=self.library_ids,
        )
        self.bookmark_store.verify_source(record, payload)

    def _bookmark_view(self, payload, projected, library_id, request):
        if self.bookmark_store is None or not isinstance(projected, dict) or projected.get("presentation") != CONTRACT:
            return
        record, unavailable = self.bookmark_store.prepare(library_id, request, payload)
        if record is not None:
            if not self.bookmark_store.is_saved(record["token"]):
                record = self.pending_bookmarks.get(record["identity"], record)
                if (
                    record["identity"] not in self.pending_bookmarks
                    and len(self.pending_bookmarks) >= self.bookmark_store.available_slots
                ):
                    record, unavailable = None, "conversation_bookmark_limit_reached"
                else:
                    self.pending_bookmarks[record["identity"]] = record
            if record is not None:
                projected["documents"][0]["sourceBookmark"] = {
                    **self.bookmark_store.visible(record),
                    "tool": self.tool_names[library_id]["read"],
                }
        if unavailable:
            projected["sourceBookmarkUnavailable"] = unavailable
            projected["sourceBookmarkNextAction"] = (
                "This source remains available through current-turn actions. No saved bookmark was created; "
                "later use a retained explicit pinned selector or rediscover through authorized tools."
            )

    def delivered(self, messages):
        """Commit only metadata for bookmark tokens actually sent successfully."""
        if self.bookmark_store is None:
            return
        delivered = set()
        for message in messages:
            if message.get("role") == "tool" and isinstance(message.get("content"), str):
                for doc in json.loads(message["content"]).get("documents", []):
                    token = ((doc.get("sourceBookmark") or {}).get("read") or {}).get("bookmark")
                    if isinstance(token, str):
                        delivered.add(token)
        records = [record for record in self.pending_bookmarks.values() if record["token"] in delivered]
        self.bookmark_store.commit(records)
        self.bookmarks_committed += len(records)
        for record in records:
            self.pending_bookmarks.pop(record["identity"], None)

    def record_request(self, call_id, operation, library_id, original, resolved):
        self.requests[call_id] = copy.deepcopy(resolved)
        self.resolutions.append(
            {
                "callId": call_id,
                "operation": operation,
                "library": library_id,
                "originalArguments": copy.deepcopy(original),
                "resolvedArguments": copy.deepcopy(resolved),
            }
        )

    def render(self, messages, owners):
        from fli_demo.navigation_view import present

        if self.closed:
            raise ReferenceError("Reference session is closed")
        introduced, calls, output = {}, {}, []
        for message in messages:
            value = copy.deepcopy(message)
            for call in value.get("tool_calls") or []:
                function = call.get("function") or {}
                calls[call["id"]] = owners.get(function.get("name"))
            if value.get("role") == "tool" and isinstance(value.get("content"), str):
                library = calls.get(value.get("tool_call_id"))
                # A shortened retained transcript may omit the earlier call. The
                # application-held call ledger still supplies its original owner.
                if library is None:
                    library = next(
                        (r["library"] for r in self.resolutions if r["callId"] == value.get("tool_call_id")), None
                    )
                if library in self.granular_libraries:
                    original = json.loads(value["content"])
                    payload = present(
                        original,
                        library_id=library,
                        register=self.register,
                        introduced=introduced,
                        request=self.requests.get(value.get("tool_call_id")),
                    )
                    self._bookmark_view(original, payload, library, self.requests.get(value.get("tool_call_id")))
                    value["content"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            output.append(value)
        return output

    def tools(self, tools, owners):
        result = copy.deepcopy(tools)
        for tool in result:
            name = tool["function"]["name"]
            operation = (
                "read"
                if name.startswith("read_documents_library_")
                else "discover"
                if name.startswith("find_documents_library_")
                else "search"
                if name.startswith("search_library_")
                else None
            )
            if operation:
                self.tool_names.setdefault(owners[name], {})[operation] = name
                self.argument_keys[(owners[name], operation)] = set(
                    tool["function"]["parameters"].get("properties", {})
                )
        for tool in result:
            f = tool["function"]
            props = f["parameters"].get("properties", {})
            if "outlineNodeId" in props.get("documents", {}).get("items", {}).get("properties", {}):
                self.granular_libraries.add(owners[f["name"]])
                f["description"] = (
                    "Read source or authored outlines. Two separate call shapes: bootstrap with "
                    '{"mode":"outline","documents":[{"documentId":"<returned ID>","generation":1,'
                    '"contentRevision":2}]} (copy actual returned pins); then copy an actions value with '
                    '{"reference":"<returned actions.outline or actions.read value>"}. Replace placeholders. '
                    "The action already fixes mode, snapshot and scope: omit mode, documents and paging when using "
                    "reference/references. Never put an action value in nodeIds/outlineNodeId. documentRef is for "
                    "search only. Compatible selected-read actions may use references. Explicit pinned canonical "
                    "selectors remain supported for diagnostics. includeText=false gives size only; "
                    "true/default delivers outline "
                    "entries or complete selected source. details=true reruns the same authorized read with "
                    "canonical diagnostic fields when exact IDs/provenance are requested. All-or-nothing "
                    "maxTokens remains 32000 by default, at most 200000. Cite returned human citations, "
                    "not navigation references or internal IDs."
                )
                props.update(
                    reference={
                        "type": "string",
                        "description": "Copy an actions value here, without mode/documents/paging. Not documentRef.",
                    },
                    references={"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string"}},
                    details={"type": "boolean", "description": "Return canonical diagnostic fields for this read."},
                )
                document = props["documents"]["items"]
                document["required"] = list(PIN)
                document["description"] = (
                    "Explicit source selector with all returned snapshot pins; "
                    "never put documentRef or an action value here."
                )
                document["properties"]["outlineNodeId"]["description"] = (
                    "Canonical source node ID for explicit outline requests only. An actions.outline value belongs "
                    "in top-level reference instead, without mode/documents."
                )
                for field in SOURCE_IDS:
                    value = document["properties"][field]
                    target = value.get("items", value)
                    target["pattern"] = r"^(?!nav[A-Za-z0-9]+-[1-9][0-9]*$).+"
                f["parameters"].pop("required", None)
                f["parameters"]["anyOf"] = [
                    _closed_alternative({"reference", *TRANSPORT}, ["reference"]),
                    _closed_alternative({"references", *TRANSPORT}, ["references"]),
                    _closed_alternative({*READ_KEYS, "details"}, ["mode", "documents"]),
                ]
                if self.bookmark_store is not None:
                    f["description"] = f["description"].replace(
                        "Two separate call shapes", "Three separate call shapes"
                    )
                    props["bookmark"] = {
                        "type": "string",
                        "description": "Copy sourceBookmark.read.bookmark; do not add reference/mode/documents/paging.",
                    }
                    f["parameters"]["anyOf"].append(_closed_alternative({"bookmark", *TRANSPORT}, ["bookmark"]))
                    f["description"] += (
                        ' A saved source selection can be reread with {"bookmark":"<returned bookmark>"}. '
                    )
                    f["description"] += (
                        "Copy sourceBookmark.read exactly. This app-owned bookmark differs from turn-local actions "
                        "and still revalidates source/access."
                    )
                    for field in SOURCE_IDS:
                        value = document["properties"][field]
                        value.get("items", value)["pattern"] = (
                            r"^(?!(?:nav[A-Za-z0-9]+-[1-9][0-9]*|sbm[0-9a-f]{24})$).+"
                        )
        for tool in result:
            f = tool["function"]
            if owners[f["name"]] in self.granular_libraries:
                f["description"] = f["description"].replace(" " + GUIDANCE, "")
                if f["name"].startswith("search_library_"):
                    properties = f["parameters"]["properties"]
                    properties["documentRef"] = {
                        "type": "string",
                        "description": "Copy documentRef only here at the top level of search, instead of document. "
                        "Never a read action or nested selector field.",
                    }
                    f["description"] += (
                        ' Scope search with {"query":"...","documentRef":"<returned documentRef>"}, '
                        "or a pinned document object; never both."
                    )
                    properties["document"]["required"] = list(PIN)
                    f["parameters"]["anyOf"] = [
                        _closed_alternative(set(properties) - {"document"}, ["query", "documentRef"]),
                        _closed_alternative(set(properties) - {"documentRef"}, ["query"]),
                    ]
        return result

    def close(self):
        self.closed = True
        self.pending_bookmarks.clear()

    def diagnostics(self):
        result = {
            "contract": CONTRACT,
            "turnLocal": True,
            "closed": self.closed,
            "targetFingerprint": hashlib.sha256(self.target.encode()).hexdigest(),
            "referenceCount": len(self.entries),
            "resolutions": copy.deepcopy(self.resolutions),
            "restoration": "References expire at the turn boundary; reacquire through authorized tools.",
        }
        if self.bookmark_store is not None:
            result["sourceBookmarksCommitted"] = self.bookmarks_committed
            result["sourceBookmarkOwnerSurvivesTurn"] = (
                not self.bookmark_store.closed and not self.bookmark_store.expired
            )
        return result
