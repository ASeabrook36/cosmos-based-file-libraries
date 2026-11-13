"""One sealed outline-to-focused-source check and private mechanical review.

Only ``question`` is model-facing. No tool flags, expected source text or authored
node IDs are supplied in the prompt. Review requires earlier successful model
delivery, not merely ordered local tool calls. Semantic answer/citation judgment
remains separate from these observations.
"""

from __future__ import annotations

import copy
import json

from fli.granular_source import node_indexes
from scripts.granular_followup_cases import RULE, _source_target
from scripts.granular_followup_cases import cases as previous_cases

SCHEMA = "fli.granular-outline-case.v1"
CASE = "outline-focused-source"
TRANSPORT_FIELDS = {"includeText", "maxTokens"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _canonical(value):
    # Unlike Python equality, this does not confuse true with generation 1.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def cases(source_bindings):
    source = previous_cases(source_bindings)[0]["sourceBinding"]
    pin = {key: source[key] for key in ("documentId", "generation", "contentRevision")}
    return [
        {
            "case": CASE,
            "sourceRole": "docx",
            "question": f"The platform has selected {source['fileName']}. Its current document selector is "
            + json.dumps(pin, sort_keys=True)
            + ". Use the authored outline to locate the damaged-vessel rule. Then read the focused source region "
            "using a selector returned by that outline. State the rule and cite its source. "
            "Do not read the whole document.",
            "expectedSourceFacts": [RULE],
            "expectedNavigation": [
                "actual nonempty authored outline delivered to the model",
                "later focused source read copies an advertised selector from that delivered outline",
                "no whole-document content-read request",
            ],
            "requiredCitationFile": source["fileName"],
            "sourceBinding": copy.deepcopy(source),
            "reviewCriteria": {
                "outlineDelivery": "A successful model input contains actual nonempty authored outline entries "
                "for the current source snapshot. A size-only inspection without entries does not qualify.",
                "selectorProvenance": "After that successful model input, a read request copies one of the delivered "
                "outline's selectors. Only includeText and maxTokens may differ; current document pins and source "
                "scope remain unchanged.",
                "focusedSource": "A successful model input receives the complete selected paragraph or its immediate "
                "authored Exceptions section/subtree, with source text and locations matching the verified "
                "publication. A broad ancestor or a whole-document fallback does not qualify; "
                "any full-content request fails this route. An explicit includeText=false size inspection "
                "does not deliver source content and is recorded separately.",
                "sourceAnswerAndCitation": "The answer accurately states the rule and cites its actual "
                "source location. "
                "Review meaning and attribution, not exact prose wording.",
            },
            "judgment": "One targeted mechanics qualification, not a benchmark score. Mechanical observations "
            "do not establish semantic answer/citation correctness or general model reliability.",
        }
    ]


def review_template(case):
    require(case.get("case") == CASE, "unknown outline case")
    return {
        "schema": SCHEMA + ".review",
        "case": CASE,
        "overallPass": None,
        "criteria": {
            key: {"requirement": value, "passed": None, "evidence": [], "notes": None}
            for key, value in case["reviewCriteria"].items()
        },
        "limitation": "Mechanical route observations do not grade the answer or citation; manual review is required.",
    }


def _requests(messages):
    for position, message in enumerate(messages):
        for tool in message.get("tool_calls") or []:
            function = tool.get("function") or {}
            if not function.get("name", "").startswith("read_documents_"):
                continue
            try:
                request = json.loads(function["arguments"])
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(request, dict) and isinstance(tool.get("id"), str):
                yield position, tool["id"], request


def _pinned(value, binding):
    keys = ("documentId", "generation", "contentRevision")
    return _canonical({key: value.get(key) for key in keys}) == _canonical({key: binding[key] for key in keys})


def _selector(request):
    return {key: value for key, value in request.items() if key not in TRANSPORT_FIELDS}


def _delivered_reads(calls, binding):
    """Track first request appearance and first successful response delivery.

    Call records must be chronological. Archived inputs are marked ``returned``
    only after checking their corresponding successful provider/model result;
    an input file's original ``started`` marker cannot establish delivery.
    """
    reads, seen, first_requests, conflicts = [], set(), {}, set()
    for call_index, call in enumerate(calls):
        if call.get("status") != "returned":
            continue
        messages = call.get("messages") or []
        requests = {}
        for position, tool_id, request in _requests(messages):
            signature = _canonical(request)
            previous = first_requests.get(tool_id)
            if previous and previous["signature"] != signature:
                conflicts.add(tool_id)
            first = first_requests.setdefault(tool_id, {"callIndex": call_index, "signature": signature})
            requests[tool_id] = {"request": request, "position": position, "firstCallIndex": first["callIndex"]}
        for position, message in enumerate(messages):
            if message.get("role") != "tool":
                continue
            tool_id = message.get("tool_call_id")
            recorded = requests.get(tool_id)
            if recorded is None or recorded["position"] >= position:
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            key = (tool_id, content)
            if key in seen:
                continue
            try:
                evidence = json.loads(content)
            except (TypeError, ValueError):
                continue
            if not isinstance(evidence, dict) or evidence.get("library", {}).get("libraryId") != binding["libraryId"]:
                continue
            request = recorded["request"]
            selections = request.get("documents")
            if not isinstance(selections, list) or len(selections) != 1 or not _pinned(selections[0], binding):
                continue
            documents = evidence.get("documents")
            if not isinstance(documents, list) or len(documents) != 1:
                continue
            document = documents[0]
            if not _pinned(document, binding) or document.get("sourceSha256") != binding["sourceSha256"]:
                continue
            # The actual prior response must also precede the request in this
            # captured history. A parallel same-model-round request cannot copy it.
            before_request = {
                m.get("tool_call_id") for m in messages[: recorded["position"]] if m.get("role") == "tool"
            }
            reads.append(
                {
                    "toolCallId": tool_id,
                    "request": request,
                    "selection": selections[0],
                    "document": document,
                    "deliveredCallIndex": call_index,
                    "requestFirstCallIndex": recorded["firstCallIndex"],
                    "toolRepliesBeforeRequest": before_request,
                }
            )
            seen.add(key)
    return [row for row in reads if row["toolCallId"] not in conflicts], sorted(conflicts)


def inspect_turn(case, turn, successful_model_calls, source_sections):
    """Inspect the actual delivered outline → copied-selector → focused-source route."""
    require(case.get("case") == CASE, "unknown outline case")
    binding = case["sourceBinding"]
    tree = source_sections[0]["sourceNavigation"]
    require(
        all(tree.get(key) == binding[key] for key in ("documentId", "generation", "sourceSha256")),
        "source publication differs from sealed binding",
    )
    source, nodes, target = _source_target(source_sections, RULE)
    containing = target["containingSectionId"]
    require(nodes[containing]["kind"] == "section", "target has no immediate authored section")
    reads, conflicts = _delivered_reads(successful_model_calls, binding)
    outlines, advertised = [], []
    for row in reads:
        entries = row["document"].get("outline")
        if row["request"].get("mode") != "outline" or not isinstance(entries, list) or not entries:
            continue
        if not all(
            entry.get("navigationNodeId") in nodes
            and entry.get("navigationKind") == nodes[entry["navigationNodeId"]]["kind"]
            and entry.get("parentNodeId") == nodes[entry["navigationNodeId"]]["parentId"]
            for entry in entries
        ):
            continue
        outlines.append(row["toolCallId"])
        for entry in entries:
            for field, selector in entry.items():
                if field.endswith("Selector") and isinstance(selector, dict) and selector.get("mode") == "selected":
                    advertised.append({"row": row, "field": field, "selector": _selector(selector)})
    copied, focused, citations = [], [], []
    for row in reads:
        if row["request"].get("mode") != "selected":
            continue
        origins = [
            origin
            for origin in advertised
            if origin["row"]["deliveredCallIndex"] < row["requestFirstCallIndex"]
            and origin["row"]["toolCallId"] in row["toolRepliesBeforeRequest"]
            and _canonical(origin["selector"]) == _canonical(_selector(row["request"]))
        ]
        if not origins:
            continue
        copied.append(
            {
                "focusedReadToolCallId": row["toolCallId"],
                "outlineToolCallId": origins[0]["row"]["toolCallId"],
                "selectorField": origins[0]["field"],
                "outlineDeliveredCallIndex": origins[0]["row"]["deliveredCallIndex"],
                "selectedRequestFirstCallIndex": row["requestFirstCallIndex"],
            }
        )
        selection, document = row["selection"], row["document"]
        if selection.get("nodeIds") not in ([target["id"]], [containing]):
            continue
        expected = [
            source_sections[i]
            for i in node_indexes(tree, selection["nodeIds"], subtree=selection.get("nodeScope") != "section")
        ]
        parts = document.get("sections") or []
        if not (
            document.get("selectionComplete") is True
            and document.get("documentComplete") is False
            and row["request"].get("includeText", True) is True
            and len(parts) == len(expected)
            and all(
                part.get("navigationNodeId") == original["location"]["sourceAtomId"]
                and part.get("text") == original["text"]
                and part.get("location") == original["location"]
                for part, original in zip(parts, expected, strict=True)
            )
        ):
            continue
        focused.append(row["toolCallId"])
        citations.extend(part.get("citation") for part in parts if part.get("navigationNodeId") == target["id"])
    messages = turn.get("messages", []) if isinstance(turn, dict) else turn.messages
    full_requests, full_inspections = set(), set()
    for group in [messages, *(call.get("messages", []) for call in successful_model_calls)]:
        for _, tool_id, request in _requests(group):
            if request.get("mode", "full") == "full":
                (full_inspections if request.get("includeText") is False else full_requests).add(tool_id)
    return {
        "schema": SCHEMA + ".observations",
        "deliveredPinnedReadCount": len(reads),
        "nonemptyOutlineDelivered": bool(outlines),
        "nonemptyOutlineReceiptIds": outlines,
        "copiedSelectorAfterEarlierModelDelivery": bool(copied),
        "copiedSelectorOrigins": copied,
        "focusedSourceDelivered": bool(focused),
        "focusedSourceReceiptIds": focused,
        "fullReadRequested": bool(full_requests),
        "fullReadRequestIds": sorted(full_requests),
        "fullInspectionRequestIds": sorted(full_inspections),
        "fullInspectionRequestCount": len(full_inspections),
        "conflictingRequestIds": conflicts,
        "mechanicalNavigationSatisfied": bool(outlines and focused) and not full_requests and not conflicts,
        "sourceCitationReview": {
            "fileName": binding["fileName"],
            "expectedSourceLocation": copy.deepcopy(source["location"]),
            "deliveredTargetCitations": citations,
            "answerCorrect": None,
            "citationCorrect": None,
        },
        "manualReview": review_template(case),
        "limitation": "Only actual earlier successful model-input delivery establishes selector origin. "
        "Mechanical route success does not establish answer/citation correctness or general quality.",
    }
