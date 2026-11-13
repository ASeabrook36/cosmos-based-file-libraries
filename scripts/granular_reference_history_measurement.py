"""Local, scripted history/restore costs for exact navigation references.

These are chosen recovery routes, not minimal costs or model-quality results.
Fresh-turn routes use run_turn. The shortening fixture deliberately retains an
application NavigationSession across two core-loop legs; no production history
shortening policy or durable registry restoration is implemented here.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path

from fli.chunking import default_tokenizer
from fli.reference_format import expanded
from fli_demo.agent import Reply, ToolCall, _run_turn, run_turn
from fli_demo.client import IndexClient
from fli_demo.navigation_refs import NavigationSession, target_of
from scripts.granular_reference_measurement import (
    _reference_argument,
    count_json,
    digest,
    measure_transcript,
    prepare_local_cases,
    run_case,
)
from scripts.qualify_granular_navigation import save_new
from scripts.retrieval_controls import private_path

MODES = ("compact", "references", "navigation")
SUMMARY = "We were reading source.docx, under the Sampling / Exceptions authored headings, about the vessel rule."
QUESTION = "Read the same complete source paragraph again with its citation."


def _last_payload(messages):
    return expanded(json.loads(next(m["content"] for m in reversed(messages) if m.get("role") == "tool")))


def _visible_rows(messages):
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        for document in expanded(json.loads(message["content"])).get("documents", []):
            yield from [*document.get("outline", []), *document.get("sections", [])]


def _action(messages, canonical, hints, presentation):
    if presentation == "navigation":
        return _reference_argument(messages, canonical, "read", hints)
    field = "outlineSelector" if canonical["mode"] == "outline" else "readSelector"
    assert len(hints) == 1, "These restore fixtures select one region per step"
    row = next(row for row in _visible_rows(messages) if all(row.get(k) == v for k, v in hints[0].items()))
    return {**copy.deepcopy(row[field]), "includeText": True, "maxTokens": 200000}


def _index(env, presentation):
    index = IndexClient(
        "http://testserver",
        "k",
        http=env.client,
        evidence_view="references" if presentation == "references" else "compact",
    )
    requests = []
    for operation, attribute in (("read", "read_documents"), ("discover", "discover_documents")):
        original = getattr(index, attribute)

        def capture(library, agent, *, _call=original, _operation=operation, **arguments):
            requests.append({"operation": _operation, "arguments": copy.deepcopy(arguments)})
            return _call(library, agent, **arguments)

        setattr(index, attribute, capture)
    return index, requests


def _agent(env):
    return {"id": "reader", "instructions": "Answer from the supplied source only.", "libraryIds": [env.lib]}


def _invoke(tools, operation, arguments, call_id):
    prefix = "find_documents_library_" if operation == "discover" else "read_documents_library_"
    name = next(tool["function"]["name"] for tool in tools if tool["function"]["name"].startswith(prefix))
    return Reply(None, [ToolCall(call_id, name, json.dumps(arguments))])


def _source_identity(payload):
    return [
        (
            row["text"],
            row.get("citation"),
            row.get("sourceStructure"),
            (row.get("location") or {}).get("originalCoordinate"),
        )
        for document in payload.get("documents", [])
        for row in document.get("sections", [])
    ]


def _verify_final(captures, canonical, expected):
    visible = _last_payload(captures[-1]["messages"])
    assert _source_identity(visible) == _source_identity(canonical) == _source_identity(expected)
    for key in ("documentId", "generation", "contentRevision"):
        assert canonical["documents"][0][key] == expected["documents"][0][key]
    assert [row["navigationNodeId"] for row in canonical["documents"][0]["sections"]] == ["p6"]
    document = visible["documents"][0]
    assert visible["selectionComplete"] and document["selectionComplete"] and not document["documentComplete"]
    assert document["fileName"] == "source.docx" and document["processingCoverage"]["sourceStructureLimitations"]
    assert document["sections"][0]["heading"] == "Exceptions"
    return visible


def run_restore(env, case, expected, presentation, route, *, expired_reference=None):
    """Fresh turn, either retained full selector, expired ref, or prose only."""
    if presentation not in MODES or route not in {"retained_selector", "expired_reference", "prose_only"}:
        raise ValueError("unsupported restore measurement")
    if route == "expired_reference" and (presentation != "navigation" or not expired_reference):
        raise ValueError("expired-reference route requires the old navigation reference")
    index, actual = _index(env, presentation)
    captures, diagnostics = [], {}
    selector = case["requests"][-1]
    summary = SUMMARY
    if route == "retained_selector":
        summary += " Retained exact read request: " + json.dumps(selector, ensure_ascii=False)
    elif route == "expired_reference":
        summary += " Previous turn's read reference: " + expired_reference
    history = [{"role": "assistant", "content": summary}]

    def chat(messages, tools):
        captures.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        number = len(captures) - 1
        if route == "retained_selector":
            return (
                _invoke(tools, "read", selector, "retained-source")
                if number == 0
                else Reply("The requested source was delivered.")
            )
        if route == "expired_reference":
            if number == 0:
                return _invoke(tools, "read", {"reference": expired_reference}, "expired-reference")
            if number == 1:
                assert _last_payload(messages)["error"] == "navigation_reference_unavailable"
            number -= 1
        if number == 0:
            return _invoke(tools, "discover", {"fileNames": ["source.docx"]}, "rediscover")
        if number == 1:
            document = _last_payload(messages)["documents"][0]
            return _invoke(
                tools,
                "read",
                {**document["outlineSelector"], "includeText": True, "maxTokens": 200000},
                "restored-outline",
            )
        step = number - 1
        if step < len(case["requests"]):
            arguments = _action(messages, case["requests"][step], case["visibleTargets"][step], presentation)
            return _invoke(tools, "read", arguments, f"restored-read-{step}")
        return Reply("The requested source was delivered.")

    turn = run_turn(
        chat,
        index,
        _agent(env),
        history,
        QUESTION,
        context_token_budget=200000,
        image_inputs=False,
        **(
            {"presentation": "navigation", "presentation_diagnostics": diagnostics}
            if presentation == "navigation"
            else {}
        ),
    )
    canonical = _last_payload(turn.messages)
    visible = _verify_final(captures, canonical, expected)
    failures = [
        json.loads(m["content"])
        for m in turn.messages
        if m.get("role") == "tool" and json.loads(m["content"]).get("error")
    ]
    assert len(failures) == int(route == "expired_reference")
    return {
        "case": "restored_" + route,
        "presentation": presentation,
        "captures": captures,
        "actualRequests": actual,
        "canonicalEvidence": canonical,
        "visibleFinalEvidence": visible,
        "diagnostics": diagnostics,
        "failedLocalReferenceCalls": len(failures),
        "route": "Retained exact selector direct read"
        if route == "retained_selector"
        else ("Expired reference rejected locally, then " if route == "expired_reference" else "")
        + "find by filename → root outline → authored heading outlines → focused paragraph",
    }


def run_shortened(env, case, expected, presentation):
    """Explicit renderer boundary fixture using the unchanged core agent loop."""
    if presentation not in MODES:
        raise ValueError("unsupported presentation")
    index, actual = _index(env, presentation)
    session = NavigationSession("reader", [env.lib], target_of(index)) if presentation == "navigation" else None
    initial, continued, diagnostics = [], [], {}

    def first_chat(messages, tools):
        initial.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        number = len(initial) - 1
        if number == len(case["requests"]):
            return Reply("The requested source was delivered.")
        arguments = (
            case["requests"][number]
            if number == 0
            else _action(messages, case["requests"][number], case["visibleTargets"][number], presentation)
        )
        return _invoke(tools, "read", arguments, f"before-trim-{number}")

    def continued_chat(messages, tools):
        continued.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if len(continued) == 1:
            row = _last_payload(messages)["documents"][0]["sections"][0]
            arguments = {"reference": row["actions"]["read"]} if presentation == "navigation" else row["readSelector"]
            return _invoke(tools, "read", {**arguments, "includeText": True, "maxTokens": 200000}, "after-trim")
        return Reply("The requested source was delivered again.")

    try:
        first = _run_turn(
            first_chat,
            index,
            _agent(env),
            [],
            QUESTION,
            context_token_budget=200000,
            image_inputs=False,
            navigation=session,
        )
        # Preserve the final complete call/reply pair; all earlier outlines and
        # their documentInfo are absent. No cached source or old dictionary is
        # consulted to resolve the next action: only the application registry.
        last_tool = max(n for n, m in enumerate(first.messages) if m.get("role") == "tool")
        retained = copy.deepcopy(first.messages[last_tool - 1 : last_tool + 1])
        assert retained[0].get("tool_calls") and retained[1]["role"] == "tool"
        before = _last_payload(initial[-1]["messages"])
        second = _run_turn(
            continued_chat,
            index,
            _agent(env),
            retained,
            QUESTION,
            context_token_budget=200000,
            image_inputs=False,
            navigation=session,
        )
        refreshed = _last_payload(continued[0]["messages"])
        canonical = _last_payload(second.messages)
        visible = _verify_final(continued, canonical, expected)
        if session:
            assert "documentInfo" not in before["documents"][0]
            assert refreshed["documents"][0]["documentInfo"]
            assert refreshed["documents"][0]["documentRef"] == before["documents"][0]["documentRef"]
            assert (
                refreshed["documents"][0]["sections"][0]["actions"] == before["documents"][0]["sections"][0]["actions"]
            )
        return {
            "case": "shortened_same_session",
            "presentation": presentation,
            "captures": [*initial, *continued],
            "initialCaptures": initial,
            "continuedCaptures": continued,
            "actualRequests": actual,
            "canonicalEvidence": canonical,
            "visibleFinalEvidence": visible,
            "beforeShorteningEvidence": before,
            "refreshedEvidence": refreshed,
            "documentInfoRefreshJsonDeltaTokensLocal": count_json(refreshed) - count_json(before),
            "diagnostics": diagnostics,
            "failedLocalReferenceCalls": 0,
            "route": "Two initial reads; retain final call/reply; refresh model view; reread its visible action.",
            "boundary": "Explicit local renderer fixture keeps one application registry across two core-loop legs. "
            "No production automatic shortening policy, provider cache, or durable restoration is tested.",
        }
    finally:
        if session:
            session.close()
            diagnostics.update(session.diagnostics())


def _metric(result):
    return {
        "case": result["case"],
        "presentation": result["presentation"],
        "route": result["route"],
        "successfulApiCalls": len(result["actualRequests"]),
        "failedLocalReferenceCalls": result["failedLocalReferenceCalls"],
        "sourceBodyAndCitationIdentitySha256": digest(_source_identity(result["canonicalEvidence"])),
        "sourceBodiesAndCitationsPreserved": True,
        **measure_transcript(result["captures"]),
        **(
            {
                "documentInfoRefreshJsonDeltaTokensLocal": result["documentInfoRefreshJsonDeltaTokensLocal"],
                "continuationOnly": measure_transcript(result["continuedCaptures"]),
                "boundary": result["boundary"],
            }
            if result["case"] == "shortened_same_session"
            else {}
        ),
    }


async def measure(*, private_output=None):
    directory = private_path(private_output) if private_output else None
    if directory:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    env, cases, fixture, _ = await prepare_local_cases()
    small = next(case for case in cases if case["case"] == "small_paragraph")
    route = next(case for case in cases if case["case"] == "original_outline_route")
    results = []
    try:
        seed = run_case(env, small, "navigation")
        expired = seed["visibleFinalEvidence"]["documents"][0]["sections"][0]["actions"]["read"]
        assert seed["diagnostics"]["closed"]
        expected = seed["canonicalEvidence"]
        for mode in MODES:
            results.append(run_shortened(env, small, expected, mode))
            for restoration in ("retained_selector", "prose_only"):
                results.append(run_restore(env, route, expected, mode, restoration))
        results.append(run_restore(env, route, expected, "navigation", "expired_reference", expired_reference=expired))
        report = {
            "schema": "fli.granular-reference.history-measurement.v1",
            "fixture": fixture,
            "tokenizer": default_tokenizer().name,
            "measurements": [_metric(result) for result in results],
            "expiredReferenceAcquisitionSetup": measure_transcript(seed["captures"]),
            "newCloudCalls": 0,
            "newModelCalls": 0,
            "providerUsage": None,
            "cosmosRu": None,
            "method": "Matched local source/API and scripted choices. Fresh restores use the normal agent adapter. "
            "Shortening is an explicit renderer fixture, not a shipped shortening policy. All counts are local "
            "JSON/tokenizer estimates including instructions, schemas, wrappers and repeated exposure. "
            "Retained-selector vs expired-reference restores intentionally have different retained information; "
            "prose-only restoration is the matched-information comparison. Routes are specified, not minimal. "
            "No provider usage, cache hit, monetary saving, model quality, latency or retrieval ranking is measured.",
        }
        if directory:
            save_new(
                directory / "expired-reference-setup.json", {key: value for key, value in seed.items() if key != "turn"}
            )
            for result in results:
                save_new(directory / f"{result['case']}-{result['presentation']}.json", result)
            save_new(directory / "measurements.json", report)
        return report
    finally:
        env.client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New private directory for captures; never a repository path")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(measure(private_output=args.output)), indent=2))


if __name__ == "__main__":
    main()
