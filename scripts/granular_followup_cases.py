"""Two targeted guidance checks; pure case preparation and private review helpers.

These reuse consumed mechanics fixtures and are not a new benchmark. Only question
is model-facing. Expected facts, criteria and source bindings are sealed controls.
No imports here construct clients, read credentials or execute a model call.
"""

from __future__ import annotations

import copy
import json
import re

SCHEMA = "fli.granular-guidance-followup.cases.v1"
CELL_FACTS = [
    "Keep the vessel sealed.",
    "Record the temperature.",
    "Notify the supervisor.",
    "These steps apply only while the vessel is in cold storage.",
]
RULE = "Do not sample a damaged vessel."


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cases(source_bindings):
    """Return two normal-harness cases for exactly one newly published DOCX pin."""
    require(isinstance(source_bindings, dict) and set(source_bindings) == {"docx"}, "DOCX-only binding required")
    source = source_bindings["docx"]
    require(isinstance(source, dict), "source binding must be an object")
    for key in ("libraryId", "documentId", "fileName", "sourceSha256"):
        require(isinstance(source.get(key), str) and source[key], "source identity incomplete")
    require(re.fullmatch(r"[a-f0-9]{64}", source["sourceSha256"]), "source hash unavailable")
    require(source["fileName"].lower().endswith(".docx"), "DOCX source required")
    require(type(source.get("generation")) is int and source["generation"] > 0, "source generation unavailable")
    require(
        type(source.get("contentRevision")) is int and source["contentRevision"] >= 0, "source revision unavailable"
    )
    pin = {key: source[key] for key in ("documentId", "generation", "contentRevision")}
    prefix = (
        f"The platform has selected {source['fileName']}. Its current document selector is "
        + json.dumps(pin, sort_keys=True)
        + ". "
    )
    specifications = [
        (
            "cell-completeness",
            "Locate the cold-storage action cell and read that complete table cell. Describe its instructions "
            "and the final qualification, preserving which paragraphs are list items and which paragraph is "
            "outside the list. Do not invent printed list numbers. Cite source table/cell coordinates.",
            CELL_FACTS,
            ["whole cell node selected and returned to the model", "qualifier retained outside the list"],
            {
                "cellScope": "A selected read requests the source-derived table-cell node with the original pins; "
                "its successful model-visible receipt contains exactly that cell's source atoms "
                "and selectionComplete=true. "
                "Search hits, a document/subtree read or bulk paragraph handles alone do not meet this check.",
                "sourceRelationships": "All four instructions/qualification are accurately represented; "
                "source paragraphs "
                "10 and 13 remain ordinary paragraphs and 11-12 list items, without invented printed ordinals.",
                "humanCitations": "Human-readable source citations identify the actual action cell and paragraphs; "
                "unrequested internal IDs are omitted. Review semantics, not exact prose wording.",
            },
        ),
        (
            "source-identifier-fields",
            "Use the current authored outline to find and read the damaged-vessel rule. State the rule and cite "
            "its source. I also need the identifiers returned for that paragraph: include a JSON object with "
            "the fields sectionId, containingSectionId and citation, copying their values as returned, and briefly "
            "explain what each identifier refers to. Also state whether a rendered page number or authored "
            "chapter number is available for this rule; do not guess unavailable coordinates.",
            [RULE],
            ["current authored outline then source read", "distinct exact returned identifier fields"],
            {
                "sourceRead": "A successful model-visible source read contains the actual damaged-vessel paragraph "
                "under its authored Exceptions section, using the current document pins, after an outline read.",
                "identifierFields": "The requested JSON fields match the same returned paragraph's sectionId, "
                "containingSectionId and citation exactly. Expected values come from this run's verified source "
                "and delivered response, never from old evidence or prompt-injected gold.",
                "identifierMeaning": "The explanation distinguishes the versioned evidence unit from its containing "
                "authored section node. A correctly copied object does not excuse contradictory prose.",
                "sourceAnswer": "Rule and citation are accurate. Explicitly disclose unavailable rendered page and "
                "authored chapter numbers; do not invent coordinates. Semantic review is required in addition "
                "to objective named-field comparisons.",
            },
        ),
    ]
    return [
        {
            "case": identifier,
            "sourceRole": "docx",
            "question": prefix + question,
            "expectedSourceFacts": copy.deepcopy(facts),
            "expectedNavigation": navigation,
            "requiredCitationFile": source["fileName"],
            "judgment": "Targeted mechanics requalification; objective observations plus independent manual review, "
            "not a benchmark score or proof of general model behavior.",
            "reviewCriteria": criteria,
            "sourceBinding": copy.deepcopy(source),
        }
        for identifier, question, facts, navigation, criteria in specifications
    ]


def review_template(case):
    return {
        "schema": "fli.granular-guidance-followup.review.v1",
        "case": case["case"],
        "overallPass": None,
        "criteria": {
            key: {"requirement": value, "passed": None, "evidence": [], "notes": None}
            for key, value in case["reviewCriteria"].items()
        },
        "limitation": "Mechanical observations do not grade semantic correctness, contradictory prose "
        "or general quality.",
    }


def _source_target(sections, text):
    matches = [s for s in sections if s.get("sourceStructure", {}).get("paragraphs", [{}])[0].get("text") == text]
    require(len(matches) == 1, "verified source must contain exactly one target paragraph")
    target = matches[0]
    tree = sections[0]["sourceNavigation"]
    require(tree["schema"] == "fli.granular-source.v1", "unexpected source navigation schema")
    nodes = {n["id"]: n for n in tree["nodes"]}
    return target, nodes, nodes[target["location"]["sourceAtomId"]]


def _reads(calls, binding):
    """Only tool replies present in a completed provider call count as delivered.

    Pass the normal harness's in-memory call records. When loading archived
    model-input files, the caller must set status=returned only when the paired
    provider/model-result receipt confirms success; input files alone are insufficient.
    """
    seen, rows = set(), []
    pin = {key: binding[key] for key in ("documentId", "generation", "contentRevision")}
    for call in calls:
        if call.get("status") != "returned":
            continue
        requests = {}
        for message in call["messages"]:
            for tool in message.get("tool_calls") or []:
                function = tool.get("function", {})
                if function.get("name", "").startswith("read_documents_"):
                    requests[tool["id"]] = json.loads(function["arguments"])
            if message.get("role") != "tool" or message.get("tool_call_id") not in requests:
                continue
            key = (message["tool_call_id"], message["content"])
            if key in seen:
                continue
            seen.add(key)
            request = requests[message["tool_call_id"]]
            evidence = json.loads(message["content"])
            for document in evidence.get("documents", []):
                if not all(document.get(k) == v for k, v in pin.items()):
                    continue
                selections = [s for s in request.get("documents", []) if all(s.get(k) == v for k, v in pin.items())]
                if selections:
                    rows.append(
                        {"toolCallId": key[0], "request": request, "selection": selections[0], "document": document}
                    )
    return rows


def _json_objects(text):
    decoder = json.JSONDecoder()
    for position, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[position:])
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


def inspect_turn(case, turn, successful_model_calls, source_sections):
    """Return objective observations, not an automatic answer grade.

    source_sections must be the run's independently validated publication artifact.
    turn may be the normal TurnResult or its JSON/asdict representation.
    """
    require(case["case"] in {"cell-completeness", "source-identifier-fields"}, "unknown followup case")
    answer = turn["answer"] if isinstance(turn, dict) else turn.answer
    binding = case["sourceBinding"]
    tree = source_sections[0]["sourceNavigation"]
    require(
        all(tree.get(key) == binding[key] for key in ("documentId", "generation", "sourceSha256")),
        "source publication differs from sealed binding",
    )
    reads = _reads(successful_model_calls, case["sourceBinding"])
    observations = {"deliveredPinnedReadCount": len(reads), "manualReview": review_template(case)}
    if case["case"] == "cell-completeness":
        _, nodes, paragraph = _source_target(source_sections, CELL_FACTS[0])
        cell = nodes[paragraph["parentId"]]
        require(cell["kind"] == "tableCell", "target is not in a source table cell")
        expected = {node["id"] for node in nodes.values() if node["parentId"] == cell["id"]}
        original = {s["location"]["sourceAtomId"]: s for s in source_sections}
        receipts = [
            row["toolCallId"]
            for row in reads
            if row["request"].get("mode") == "selected"
            and row["selection"].get("nodeIds") == [cell["id"]]
            and row["document"].get("selectionComplete") is True
            and {s.get("navigationNodeId") for s in row["document"].get("sections", [])} == expected
            and len(row["document"]["sections"]) == len(expected)
            and all(
                s["text"] == original[s["navigationNodeId"]]["text"]
                and s["location"] == original[s["navigationNodeId"]]["location"]
                for s in row["document"]["sections"]
            )
        ]
        observations.update(
            cellNodeId=cell["id"],
            expectedSourceAtoms=sorted(expected),
            completeCellReadDelivered=bool(receipts),
            completeCellReceiptIds=receipts,
        )
    else:
        source, _, target = _source_target(source_sections, RULE)
        outlines = [i for i, row in enumerate(reads) if row["request"].get("mode") == "outline"]
        records = [
            (i, part)
            for i, row in enumerate(reads)
            for part in row["document"].get("sections", [])
            if part.get("navigationNodeId") == target["id"]
        ]
        expected = [
            {key: part.get(key) for key in ("sectionId", "containingSectionId", "citation")} for _, part in records
        ]
        returned = [
            obj for obj in _json_objects(answer) if set(obj) >= {"sectionId", "containingSectionId", "citation"}
        ]
        observations.update(
            sourceParagraphDelivered=bool(records),
            sourceParagraphTextMatches=bool(records)
            and all(
                part.get("text") == source["text"] and part.get("location") == source["location"] for _, part in records
            ),
            outlineBeforeSourceRead=any(i < j for i in outlines for j, _ in records),
            expectedReturnedFields=expected,
            returnedAuthoredContainerMatchesSource=bool(expected)
            and all(
                item["containingSectionId"] == target["containingSectionId"]
                and item["sectionId"] != item["containingSectionId"]
                for item in expected
            ),
            reportedFieldObjects=returned,
            exactReportedFields=bool(returned)
            and bool(expected)
            and all(
                {key: item[key] for key in ("sectionId", "containingSectionId", "citation")} in expected
                for item in returned
            ),
        )
    return observations
