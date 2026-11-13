"""Bounded local presentation measurements through the normal agent adapter.

No cloud services, credentials, real answering models, ranking measurements or
provider cost estimates. The source fixtures are authored mechanics examples,
not a confirmation corpus. Local counters include instructions, schemas, tool
wrappers and repeated history; they are not provider token usage.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from docx import Document

from fli.chunking import default_tokenizer
from fli.reference_format import CONTRACT, expanded, model_messages, pack, unpack
from fli_demo.agent import Reply, ToolCall, run_turn
from fli_demo.client import IndexClient
from fli_demo.responses import azure_responses_chat
from scripts.qualify_granular_navigation import fixtures, save_new
from scripts.retrieval_controls import private_path

SELECTOR_FIELDS = {
    "documentSelector",
    "outlineSelector",
    "readSelector",
    "subtreeSelector",
    "fullSelector",
    "parentSelector",
    "containingSectionSelector",
    "selectorGuidance",
    "readCapabilities",
}


def count_json(value):
    return len(default_tokenizer().encode(json.dumps(value, ensure_ascii=False, separators=(",", ":"))))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def omit(value, fields):
    if isinstance(value, list):
        return [omit(item, fields) for item in value]
    if isinstance(value, dict):
        return {key: omit(item, fields) for key, item in value.items() if key not in fields}
    return value


def source_texts(value):
    if isinstance(value, list):
        return [text for item in value for text in source_texts(item)]
    if not isinstance(value, dict):
        return []
    own = (
        [value["text"]]
        if isinstance(value.get("text"), str)
        and value.get("textSource", "extracted_source_text") == "extracted_source_text"
        else []
    )
    return own + [text for key, item in value.items() if key != "text" for text in source_texts(item)]


def preview_texts(value):
    if isinstance(value, list):
        return [text for item in value for text in preview_texts(item)]
    if not isinstance(value, dict):
        return []
    preview = value.get("navigationPreview") or {}
    own = [preview["prefix"]] if isinstance(preview, dict) and isinstance(preview.get("prefix"), str) else []
    return own + [text for key, item in value.items() if key != "navigationPreview" for text in preview_texts(item)]


def measure_payload(evidence):
    """Keep source, useful navigation and serialization estimates distinct."""
    tokenizer = default_tokenizer()
    total = count_json(evidence)
    semantic = expanded(evidence)
    encode = pack if evidence.get("contract") == CONTRACT else lambda value: value
    no_selectors = omit(semantic, SELECTOR_FIELDS | {"actions"})
    no_previews = omit(no_selectors, {"navigationPreview"})
    no_source = omit(no_previews, {"text"})
    result = {
        "evidenceJsonTokensLocal": total,
        "renderedSourceTextTokensLocal": sum(len(tokenizer.encode(text)) for text in source_texts(semantic)),
        "navigationPreviewTextTokensLocal": sum(len(tokenizer.encode(text)) for text in preview_texts(semantic)),
        "orderedSelectorRemovalDeltaTokensLocal": total - count_json(encode(no_selectors)),
        "orderedPreviewRemovalDeltaTokensLocal": count_json(encode(no_selectors)) - count_json(encode(no_previews)),
        "orderedSourceRemovalDeltaTokensLocal": count_json(encode(no_previews)) - count_json(encode(no_source)),
        "orderedRemainingNavigationProtocolTokensLocal": count_json(encode(no_source)),
    }
    assert sum(value for key, value in result.items() if key.startswith("ordered")) == total
    return result


def measure_transcript(captures):
    """Count actual chat-callback inputs, never substitute API-only JSON totals."""
    rows, unique = [], {}
    repeated = Counter()
    for number, capture in enumerate(captures, 1):
        messages, tools = capture["messages"], capture["tools"]
        system = [message for message in messages if message["role"] in {"system", "developer"}]
        tool_messages = [message for message in messages if message["role"] == "tool"]
        for message in tool_messages:
            content = message["content"]
            key = (message.get("tool_call_id"), hashlib.sha256(content.encode()).hexdigest())
            unique[key] = message
            repeated.update(measure_payload(json.loads(content)))
        rows.append(
            {
                "modelInput": number,
                "messagesAndToolsJsonTokensLocal": count_json({"messages": messages, "tools": tools}),
                "agentBudgetSerializationTokensLocal": len(
                    default_tokenizer().encode(json.dumps({"messages": messages, "tools": tools}))
                ),
                "toolSchemaJsonTokensLocal": count_json(tools),
                "systemInstructionsTextTokensLocal": sum(len(default_tokenizer().encode(m["content"])) for m in system),
                "systemMessagesJsonTokensLocal": count_json(system),
                "toolReplyExposures": len(tool_messages),
            }
        )
    unique_rows = []
    for message in unique.values():
        payload = json.loads(message["content"])
        unique_rows.append({**measure_payload(payload), "toolMessageJsonTokensLocal": count_json(message)})
    return {
        "modelInputs": rows,
        "cumulativeMessagesAndToolsTokensLocal": sum(row["messagesAndToolsJsonTokensLocal"] for row in rows),
        "cumulativeAgentBudgetSerializationTokensLocal": sum(
            row["agentBudgetSerializationTokensLocal"] for row in rows
        ),
        "cumulativeSchemaJsonTokensLocal": sum(row["toolSchemaJsonTokensLocal"] for row in rows),
        "cumulativeSystemInstructionsTextTokensLocal": sum(row["systemInstructionsTextTokensLocal"] for row in rows),
        "toolReplyExposures": sum(row["toolReplyExposures"] for row in rows),
        "uniqueToolReplies": len(unique),
        "uniqueToolReplyMeasurements": unique_rows,
        "uniqueToolReplyTotals": dict(sum((Counter(row) for row in unique_rows), Counter())),
        "repeatedToolReplyExposureTotals": dict(repeated),
        "counterStatus": "local_tokenizer_estimates;no_provider_usage_measured",
    }


def compare_existing_references(captures):
    encoded = [{"messages": model_messages(row["messages"]), "tools": copy.deepcopy(row["tools"])} for row in captures]
    for original, rewritten in zip(captures, encoded, strict=True):
        for before, after in zip(original["messages"], rewritten["messages"], strict=True):
            if before.get("role") == "tool":
                assert unpack(json.loads(after["content"])) == expanded(json.loads(before["content"]))
            else:
                assert before == after
        assert original["tools"] == rewritten["tools"]
    return {
        "compact": measure_transcript(captures),
        "references": measure_transcript(encoded),
        "exactExpandedEvidencePreserved": True,
    }


def measure_retained(prior_root):
    """Replay a retained complete, single-case/single-attempt trace without a model.

    The complete private root is fingerprinted before and after. No body text,
    endpoint or private identifier is returned in the aggregate report.
    """
    root = private_path(prior_root)

    def snapshot():
        paths = sorted(path for path in root.rglob("*") if path.is_file())
        if any(path.is_symlink() for path in paths):
            raise ValueError("retained evidence must not contain linked external files")
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    before = snapshot()
    if not before:
        raise ValueError("retained evidence root is empty")

    def read(relative):
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("retained evidence path escapes its root")
        return json.loads(path.read_text())

    controls = read("models/controls.json")
    status = read("models/status.json")
    if len(controls.get("cases", [])) != 1 or status.get("status") != "complete_pending_source_review":
        raise ValueError("baseline reconstruction requires one completed retained case")
    name = controls["cases"][0]["case"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]+", name):
        raise ValueError("invalid retained case name")
    started = read("models/started.json")
    ledger = read("models/provider/ledger.json")
    if ledger["logicalCallsStarted"] != ledger["physicalResponsesAttempts"] or ledger["unmeteredAttempts"]:
        raise ValueError("baseline reconstruction requires completed single-attempt calls")
    base = Path("models") / name
    captures = [read(path.relative_to(root)) for path in sorted((root / base).glob("model-input-*.json"))]
    matched, provider = [], Counter()

    def create(**request):
        number = len(matched) + 1
        if request != read(f"models/provider/request-{number:05}.json"):
            raise ValueError("normal Responses replay differs from retained wire request")
        response = read(f"models/provider/response-{number:05}.json")
        if response.get("status") != "completed":
            raise ValueError("retained response is incomplete")
        usage = response["usage"]
        provider.update(inputTokens=usage["input_tokens"], outputTokens=usage["output_tokens"])
        matched.append(digest(request))
        return response

    adapter = azure_responses_chat(
        "unused-offline-reconstruction",
        started["deployment"],
        client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        reasoning_effort="medium",
        max_output_tokens=8000,
    )
    for capture in captures:
        adapter(capture["messages"], capture["tools"])
    assert len(matched) == len(captures) == ledger["physicalResponsesAttempts"]
    assert provider["inputTokens"] == ledger["knownInputTokens"]
    assert provider["outputTokens"] == ledger["knownOutputTokens"]
    comparisons = compare_existing_references(captures)
    final = json.loads(
        [message for message in captures[-1]["messages"] if message.get("role") == "tool"][-1]["content"]
    )
    selected = final["documents"][0]["sections"]
    plain_source_tokens = None
    publications = list(root.glob("*publication*.json"))
    if len(publications) == 1:
        sections = read(publications[0].relative_to(root))["sections"]
        selected_atoms = {row.get("navigationNodeId") for row in selected}
        originals = [row for row in sections if row.get("location", {}).get("sourceAtomId") in selected_atoms]
        if len(originals) == len(selected) and all("paragraphs" in row.get("sourceStructure", {}) for row in originals):
            plain_source_tokens = sum(
                len(default_tokenizer().encode(p["text"]))
                for row in originals
                for p in row["sourceStructure"]["paragraphs"]
            )
    after = snapshot()
    if before != after:
        raise ValueError("retained evidence changed during reconstruction")
    return {
        "schema": "fli.granular-reference.retained-baseline.v1",
        "priorFilesVerifiedUnchanged": len(before),
        "priorManifestSha256": digest(before),
        "normalResponsesWireRequestsMatched": len(matched),
        "wireRequestSha256": matched,
        "finalModelToolReply": {**measure_payload(final), "plainOriginalParagraphTokensLocal": plain_source_tokens},
        "formatCounterfactual": comparisons,
        "retainedProviderUsage": dict(provider),
        "newCloudCalls": 0,
        "newModelCalls": 0,
        "limitation": "Reference formatting is an offline same-trace counterfactual. Provider counters belong "
        "only to the original compact run; no new model quality, latency or monetary savings measured.",
    }


def source_fixture():
    """The existing tiny DOCX plus an explicit ~5k-token authored runbook section.

    The long section tests serialization scaling only. Its repeated procedure
    structure is not used for search, ranking, recall or model-quality evidence.
    """
    document = Document(io.BytesIO(fixtures()["source.docx"]))
    document.core_properties.author = ""
    document.add_heading("Extended audit runbook", level=1)
    subjects = ["receiving", "storage", "inspection", "sampling", "transport", "review", "reporting", "closure"]
    roles = [
        "receiving clerk",
        "storage custodian",
        "inspection lead",
        "sampling officer",
        "transport coordinator",
        "review secretary",
    ]
    paragraphs, tokens = [], 0
    for number in range(1, 100):
        subject, role = subjects[(number - 1) % len(subjects)], roles[(number - 1) % len(roles)]
        text = (
            f"Record {number} governs the {subject} review performed by the {role}. "
            "Before starting, compare the signed handover record with the current vessel label "
            "and record any discrepancy. Keep the original entry visible when making a correction; "
            "add the reason, date and responsible person's initials. "
            "If a required record is missing, stop this review and request that record from its custodian. "
            "Resume only after the missing evidence has been supplied and the reviewer has checked its identity. "
            "At completion, link the review outcome to the source record and retain both with the audit package."
        )
        paragraphs.append(text)
        document.add_paragraph(text)
        tokens += len(default_tokenizer().encode(text))
        if tokens >= 5000:
            break
    assert 5000 <= tokens <= 5200
    output = io.BytesIO()
    document.save(output)
    # A stable ZIP timestamp prevents wall-clock packaging differences from
    # looking like a changed source fixture between local repeated runs.
    stable = io.BytesIO()
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(stable, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, source.read(name))
    return stable.getvalue(), {"longSectionPlainSourceTokens": tokens, "longSectionParagraphs": len(paragraphs)}


async def prepare_local_cases():
    from tests.test_granular_navigation import ready

    source, fixture = source_fixture()
    env = await ready(source=source, name="source.docx")
    full = env.index.read_documents(env.lib, "reader", mode="full", documents=[env.pin], maxTokens=200000)["evidence"]
    sections = full["documents"][0]["sections"]
    by_atom = {row["navigationNodeId"]: row for row in sections}
    long_heading = next(row for row in sections if "Extended audit runbook" in row["text"])
    long_section = long_heading["containingSectionId"]

    def outline(node=None):
        pin = {**env.pin, **({"outlineNodeId": node} if node else {})}
        return {"mode": "outline", "documents": [pin], "includeText": True, "maxTokens": 200000}

    def selected(ids, scope=None):
        pin = {**env.pin, "nodeIds": ids, **({"nodeScope": scope} if scope else {})}
        return {"mode": "selected", "documents": [pin], "includeText": True, "maxTokens": 200000}

    def case(label, initial, selection, expected, action="read"):
        return {"case": label, "requests": [initial, selection], "expectedAtoms": expected, "action": action}

    all_ids = [row["navigationNodeId"] for row in sections]
    cell_ids = [node for node in all_ids if by_atom[node]["parentNodeId"] == "t0:r1:c1"]
    table_ids = [node for node in all_ids if by_atom[node]["parentNodeId"].startswith("t0:")]
    sampling_own = [node for node in all_ids if by_atom[node]["containingSectionId"] == "h3"]
    sampling_tree = [node for node in all_ids if by_atom[node]["containingSectionId"] in {"h3", "h5"}]
    long_ids = [node for node in all_ids if by_atom[node]["containingSectionId"] == long_section]
    cases = [
        case("small_paragraph", outline("h5"), selected(["p6"]), ["p6"]),
        case("multiple_paragraphs", outline("t0:r1:c1"), selected(cell_ids[:2]), cell_ids[:2]),
        case("complete_cell", outline("t0"), selected(["t0:r1:c1"]), cell_ids),
        case("complete_table", outline("h5"), selected(["t0"]), table_ids),
        case("authored_section", outline("h1"), selected(["h3"], "section"), sampling_own),
        case("authored_subtree", outline("h1"), selected(["h3"]), sampling_tree, "subtree"),
        case("source_approximately_5000_tokens", outline(), selected([long_section], "section"), long_ids),
        {
            "case": "original_outline_route",
            "requests": [outline(), outline("h1"), outline("h3"), outline("h5"), selected(["p6"])],
            "expectedAtoms": ["p6"],
            "action": "read",
        },
        {
            "case": "delayed_within_turn",
            "requests": [outline("h5"), selected(["p6"]), outline("t0"), selected(["p6"])],
            "expectedAtoms": ["p6"],
            "action": "read",
        },
    ]
    for specification in cases:
        visible_rows = []
        specification["visibleTargets"] = []
        for number, selection in enumerate(specification["requests"]):
            pin = selection["documents"][0]
            targets = pin.get("nodeIds") or [pin.get("outlineNodeId")]
            hints = []
            if number:
                for target in targets:
                    match = next(row for row in reversed(visible_rows) if row["navigationNodeId"] == target)
                    hints.append(
                        {key: match[key] for key in ("navigationKind", "title", "navigationPreview") if key in match}
                    )
            specification["visibleTargets"].append(hints)
            if selection["mode"] == "outline":
                preview = env.index.read_documents(env.lib, "reader", **selection)["evidence"]
                visible_rows.extend(preview["documents"][0]["outline"])
    return env, cases, {"sourceSha256": hashlib.sha256(source).hexdigest(), **fixture}, by_atom


def _reference_argument(messages, canonical, action, hints):
    """Use only a handle actually present in an earlier model-facing reply."""
    desired = "outline" if canonical["mode"] == "outline" else action
    references = []
    for hint in hints:
        found = None
        for message in reversed(messages):
            if message.get("role") != "tool":
                continue
            evidence = json.loads(message["content"])
            for doc in evidence.get("documents", []):
                for row in [*doc.get("outline", []), *doc.get("sections", [])]:
                    if all(row.get(key) == value for key, value in hint.items()):
                        found = (row.get("actions") or {}).get(desired)
                        if found:
                            break
        if not found:
            raise AssertionError("The scripted action lacks its human label in preceding model-facing replies")
        references.append(found)
    assert references
    return {
        **({"reference": references[0]} if len(references) == 1 else {"references": references}),
        "includeText": True,
        "maxTokens": 200000,
    }


def run_case(env, case, presentation):
    index = IndexClient(
        "http://testserver",
        "k",
        http=env.client,
        evidence_view="references" if presentation == "references" else "compact",
    )
    captures, actual_requests = [], []
    original_read = index.read_documents

    def read(library_id, agent_id, **request):
        actual_requests.append(copy.deepcopy(request))
        return original_read(library_id, agent_id, **request)

    index.read_documents = read

    def chat(messages, tools):
        captures.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        number = len(captures) - 1
        if number == len(case["requests"]):
            return Reply("The requested source region has been delivered.", [])
        canonical = copy.deepcopy(case["requests"][number])
        args = (
            _reference_argument(messages, canonical, case["action"], case["visibleTargets"][number])
            if presentation == "navigation" and number
            else canonical
        )
        name = next(
            tool["function"]["name"] for tool in tools if tool["function"]["name"].startswith("read_documents_library_")
        )
        return Reply(None, [ToolCall(f"read-{number + 1}", name, json.dumps(args))])

    diagnostics = {}
    kwargs = (
        {"presentation": "navigation", "presentation_diagnostics": diagnostics} if presentation == "navigation" else {}
    )
    turn = run_turn(
        chat,
        index,
        {"id": "reader", "instructions": "Answer from the supplied source only.", "libraryIds": [env.lib]},
        [],
        "Read the specified complete source region and retain its source citation and scope.",
        context_token_budget=200000,
        image_inputs=False,
        **kwargs,
    )
    assert len(captures) == len(case["requests"]) + 1
    assert len(actual_requests) == len(case["requests"])
    canonical = expanded(
        json.loads([message for message in turn.messages if message.get("role") == "tool"][-1]["content"])
    )
    document = canonical["documents"][0]
    atoms = document["sections"]
    assert [row["navigationNodeId"] for row in atoms] == case["expectedAtoms"]
    assert (
        canonical["selectionComplete"] is True
        and document["selectionComplete"] is True
        and document["documentComplete"] is False
    )
    visible = expanded(json.loads([m for m in captures[-1]["messages"] if m.get("role") == "tool"][-1]["content"]))
    visible_document = visible["documents"][0]
    visible_atoms = visible_document["sections"]
    assert len(visible_atoms) == len(atoms)
    for canonical_atom, visible_atom in zip(atoms, visible_atoms, strict=True):
        for field in ("text", "textSource", "citation", "sourceStructure"):
            assert canonical_atom.get(field) == visible_atom.get(field), (
                "Model-facing source/citation/structure changed"
            )
        assert canonical_atom["location"].get("originalCoordinate") == visible_atom["location"].get(
            "originalCoordinate"
        )
    assert visible_document["selectionComplete"] is True and visible_document["documentComplete"] is False
    return {
        "captures": captures,
        "canonicalEvidence": canonical,
        "actualRequests": actual_requests,
        "finalAtoms": atoms,
        "turn": turn,
        "diagnostics": diagnostics,
        "visibleFinalEvidence": visible,
    }


async def measure(presentations=("compact", "references", "navigation"), *, private_output=None):
    if not presentations or any(mode not in {"compact", "references", "navigation"} for mode in presentations):
        raise ValueError("unknown or empty presentation comparison")
    directory = private_path(private_output) if private_output else None
    if directory:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    env, cases, fixture, by_atom = await prepare_local_cases()
    rows = []
    try:
        for case in cases:
            baseline = None
            for presentation in presentations:
                result = run_case(env, case, presentation)
                atoms = result["finalAtoms"]
                preserved = [
                    (row["navigationNodeId"], row["text"], row.get("citation"), row.get("location")) for row in atoms
                ]
                if baseline is None:
                    baseline = preserved
                else:
                    assert preserved == baseline
                assert all(row["text"] == by_atom[row["navigationNodeId"]]["text"] for row in atoms)
                metric = measure_transcript(result["captures"])
                rows.append(
                    {
                        "case": case["case"],
                        "presentation": presentation,
                        "selectedSourceAtoms": len(atoms),
                        "renderedSourceTokens": sum(len(default_tokenizer().encode(row["text"])) for row in atoms),
                        "sourceBodyAndCitationIdentitySha256": digest(preserved),
                        "sourceBodiesAndCitationsPreserved": True,
                        "completeSelection": True,
                        **metric,
                    }
                )
                if directory:
                    save_new(
                        directory / f"{case['case']}-{presentation}.json",
                        {
                            "case": case,
                            "presentation": presentation,
                            "captures": result["captures"],
                            "canonicalEvidence": result["canonicalEvidence"],
                            "actualRequests": result["actualRequests"],
                            "diagnostics": result["diagnostics"],
                        },
                    )
    finally:
        env.client.close()
    report = {
        "schema": "fli.granular-reference.local-measurement.v1",
        "fixture": fixture,
        "tokenizer": default_tokenizer().name,
        "measurements": rows,
        "newCloudCalls": 0,
        "newModelCalls": 0,
        "providerUsage": None,
        "cosmosRu": None,
        "repeatability": "Source bytes are deterministic and all three presentations use the same local source "
        "identity within a comparison. Generated library/document IDs and secure turn-reference namespaces can "
        "change token counts slightly between runs; private captures freeze each reported run exactly.",
        "method": "Same local API/worker source and semantic selections, normal agent chat-callback captures, "
        "deterministic scripted decisions, fake stores/embeddings. Cumulative messages+tools include actual "
        "instructions, schemas, wrappers and repeated history. No accuracy, recall, live model behavior or monetary "
        "saving established. Ordered field-removal estimates are order-dependent and include useful navigation, "
        "not a waste classification.",
    }
    if directory:
        save_new(directory / "measurements.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New private directory outside the repository for full captures")
    parser.add_argument("--retained", type=Path, help="Read-only reconstruction of one completed private baseline")
    parser.add_argument(
        "--presentations",
        nargs="+",
        choices=("compact", "references", "navigation"),
        default=["compact", "references", "navigation"],
    )
    args = parser.parse_args()
    if args.retained:
        result = measure_retained(args.retained)
        if args.output:
            output = private_path(args.output)
            if output.is_relative_to(private_path(args.retained)):
                raise ValueError("baseline output must not modify the retained evidence root")
            output.mkdir(parents=True, mode=0o700, exist_ok=False)
            save_new(output / "retained-baseline.json", result)
    else:
        result = asyncio.run(measure(tuple(args.presentations), private_output=args.output))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
