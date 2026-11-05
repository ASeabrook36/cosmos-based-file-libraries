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
import secrets

from fli.selector_hints import GUIDANCE

CONTRACT = "fli.navigation.v1"
PIN = ("documentId", "generation", "contentRevision")
READ_KEYS = {"mode", "documents", "includeText", "maxTokens", "afterSection", "sectionLimit"}
SELECTOR_KEYS = {*PIN, "nodeIds", "nodeScope", "outlineNodeId", "sectionIds", "evidenceIds", "paragraphs"}
TRANSPORT = {"includeText", "maxTokens", "details"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def target_of(index):
    """Bind the registry to this client's API target, without adding it to model context."""
    return str(getattr(getattr(index, "http", None), "base_url", "in-process:" + str(id(index))))


class ReferenceError(ValueError):
    """Content-free error: never echo a rejected reference or source selector."""

    def payload(self):
        return {
            "error": "navigation_reference_unavailable",
            "errorCategory": "navigation_reference_unavailable",
            "complete": False,
            "nextAction": "Use a reference returned in this turn for this library, without scope overrides. "
            "After a restored/new conversation, rediscover the file and request its outline. "
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
    def __init__(self, agent_id, library_ids, target, *, namespace=None):
        self.agent_id, self.library_ids, self.target = agent_id, frozenset(library_ids), target
        self.namespace = namespace or secrets.token_hex(6)
        if not isinstance(self.namespace, str) or not self.namespace.isalnum():
            raise ValueError("invalid navigation namespace")
        self.entries, self.keys, self.requests, self.resolutions = {}, {}, {}, []
        self.closed = False
        self.granular_libraries = set()

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
        if operation == "read" and ("reference" in result or "references" in result):
            if set(result) - {"reference", "references", *TRANSPORT} or ("reference" in result) == (
                "references" in result
            ):
                raise ReferenceError("A reference cannot be combined with scope overrides")
            refs = [result["reference"]] if "reference" in result else result["references"]
            if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
                raise ReferenceError("One to twenty exact references required")
            resolved = combine([self.lookup(ref, "read", library_id, agent_id, self.target) for ref in refs])
            result = {**resolved, **{k: v for k, v in result.items() if k in TRANSPORT}}
        elif operation == "search" and "documentRef" in result:
            if "document" in result:
                raise ReferenceError("A document reference cannot override a selector")
            result["document"] = self.lookup(result.pop("documentRef"), "document", library_id, agent_id, self.target)
        elif any(key in result for key in ("reference", "references", "documentRef")):
            raise ReferenceError("Reference operation differs")
        if "details" in result and (operation != "read" or type(result["details"]) is not bool):
            raise ReferenceError("details must be a read boolean")
        return result

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
                    payload = present(
                        json.loads(value["content"]),
                        library_id=library,
                        register=self.register,
                        introduced=introduced,
                        request=self.requests.get(value.get("tool_call_id")),
                    )
                    value["content"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            output.append(value)
        return output

    def tools(self, tools, owners):
        result = copy.deepcopy(tools)
        for tool in result:
            f = tool["function"]
            props = f["parameters"].get("properties", {})
            if "outlineNodeId" in props.get("documents", {}).get("items", {}).get("properties", {}):
                self.granular_libraries.add(owners[f["name"]])
                f["description"] = (
                    "Read this library's source or authored outline. Copy an actions reference with reference; "
                    "combine compatible selected reads with references. Explicit documents remain available for "
                    "the first outline/full read. A reference fixes its snapshot and scope: do not also supply "
                    "mode/documents/paging. includeText=false gives size only; true/default delivers outline "
                    "entries or complete selected source. details=true reruns the same authorized read with "
                    "canonical diagnostic fields when exact IDs/provenance are requested. All-or-nothing "
                    "maxTokens remains 32000 by default, at most 200000. Cite returned human citations, "
                    "not navigation references or internal IDs."
                )
                props.update(
                    reference={"type": "string", "description": "Copy an actions reference from this turn."},
                    references={"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string"}},
                    details={"type": "boolean", "description": "Return canonical diagnostic fields for this read."},
                )
                f["parameters"].pop("required", None)
                f["parameters"]["anyOf"] = [{"required": [key]} for key in ("reference", "references", "documents")]
        for tool in result:
            f = tool["function"]
            if owners[f["name"]] in self.granular_libraries:
                f["description"] = f["description"].replace(" " + GUIDANCE, "")
                if f["name"].startswith("search_library_"):
                    f["parameters"]["properties"]["documentRef"] = {
                        "type": "string",
                        "description": "Returned documentRef; alternative to document selector.",
                    }
        return result

    def close(self):
        self.closed = True

    def diagnostics(self):
        return {
            "contract": CONTRACT,
            "turnLocal": True,
            "closed": self.closed,
            "targetFingerprint": hashlib.sha256(self.target.encode()).hexdigest(),
            "referenceCount": len(self.entries),
            "resolutions": copy.deepcopy(self.resolutions),
            "restoration": "References expire at the turn boundary; reacquire through authorized tools.",
        }
