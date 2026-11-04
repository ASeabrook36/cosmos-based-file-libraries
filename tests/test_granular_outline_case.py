"""Actual local API evidence and adversarial outline/selector-delivery chronology."""

import copy
import json

import pytest

from scripts import granular_outline_case as outline
from scripts.qualify_granular_navigation import fixtures
from tests.test_granular_live_models import source_bindings
from tests.test_granular_navigation import read, ready


def append_delivery(calls, request, evidence, tool_id):
    history = copy.deepcopy(calls[-1]["messages"]) if calls else []
    history.extend(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tool_id,
                        "function": {
                            "name": "read_documents_library_reports",
                            "arguments": json.dumps(request),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": tool_id, "content": json.dumps(evidence)},
        ]
    )
    calls.append({"status": "returned", "logicalCall": len(calls) + 1, "messages": history})


def modify_messages(calls, tool_id, *, request=None, evidence=None):
    for call in calls:
        for message in call["messages"]:
            for tool in message.get("tool_calls") or []:
                if tool["id"] == tool_id and request is not None:
                    tool["function"]["arguments"] = json.dumps(request)
            if message.get("tool_call_id") == tool_id and evidence is not None:
                message["content"] = json.dumps(evidence)


async def prepared(scope="section"):
    env = await ready(source=fixtures()["source.docx"], name="source.docx")
    source = json.loads(await env.s.blobs.download(env.doc["fullText"]["blobPath"]))
    binding = {
        "libraryId": env.lib,
        "documentId": env.doc["id"],
        "generation": env.doc["activeGeneration"],
        "contentRevision": env.doc["contentRevision"],
        "fileName": env.doc["fileName"],
        "sourceSha256": env.doc["sha256"],
    }
    case = outline.cases({"docx": binding})[0]
    calls, steps = [], []
    selector = {"mode": "outline", "documents": [env.pin]}
    for number, title in enumerate(("Operating rules", "Sampling", "Exceptions")):
        evidence = read(env, selector)
        entry = next(row for row in evidence["documents"][0]["outline"] if row.get("title") == title)
        steps.append((copy.deepcopy(selector), evidence, entry))
        append_delivery(calls, selector, evidence, f"outline{number}")
        selector = entry["outlineSelector"]
    if scope == "paragraph":
        evidence = read(env, selector)
        # An authored heading is represented separately; pick the rule paragraph
        # using the independently verified source atom, not a guessed ID.
        rule = next(row for row in source if outline.RULE in row["text"])
        target = next(
            row
            for row in evidence["documents"][0]["outline"]
            if row["navigationNodeId"] == rule["location"]["sourceAtomId"]
        )
        steps.append((copy.deepcopy(selector), evidence, target))
        append_delivery(calls, selector, evidence, "paragraph-outline")
        selector = target["readSelector"]
    else:
        selector = steps[-1][2]["readSelector" if scope == "section" else "subtreeSelector"]
    selected = read(env, selector)
    append_delivery(calls, selector, selected, "focused")
    return env, case, source, calls, selector, selected, steps


def inspect(case, source, calls, *, answer="Source-based answer needs independent review.", messages=None):
    return outline.inspect_turn(case, {"answer": answer, "messages": messages or calls[-1]["messages"]}, calls, source)


def test_one_case_has_only_requested_criteria_and_no_flag_coaching_or_gold():
    case = outline.cases({"docx": source_bindings()["docx"]})[0]
    assert case["case"] == "outline-focused-source"
    assert "selector returned by that outline" in case["question"]
    assert "Do not read the whole document" in case["question"]
    assert "includeText" not in case["question"] and outline.RULE not in case["question"]
    assert "h5" not in case["question"] and "ev1." not in case["question"]
    assert set(case["reviewCriteria"]) == {
        "outlineDelivery",
        "selectorProvenance",
        "focusedSource",
        "sourceAnswerAndCitation",
    }
    assert "numbering" not in json.dumps(case["reviewCriteria"])
    assert len(outline.cases({"docx": source_bindings()["docx"]})) == 1
    with pytest.raises(ValueError, match="DOCX-only"):
        outline.cases(source_bindings())


@pytest.mark.parametrize("scope", ["paragraph", "section", "subtree"])
async def test_actual_outline_to_advertised_focused_selector_is_mechanically_qualified(scope):
    _, case, source, calls, _, _, _ = await prepared(scope)
    result = inspect(case, source, calls)
    assert result["nonemptyOutlineDelivered"] and result["copiedSelectorAfterEarlierModelDelivery"]
    assert result["focusedSourceDelivered"] and not result["fullReadRequested"]
    assert result["mechanicalNavigationSatisfied"]
    assert result["sourceCitationReview"]["deliveredTargetCitations"]
    assert result["sourceCitationReview"]["answerCorrect"] is None
    assert result["sourceCitationReview"]["citationCorrect"] is None
    assert result["manualReview"]["overallPass"] is None
    assert all(row["passed"] is None for row in result["manualReview"]["criteria"].values())
    assert all(
        row["outlineDeliveredCallIndex"] < row["selectedRequestFirstCallIndex"]
        for row in result["copiedSelectorOrigins"]
    )


async def test_transport_budget_and_include_text_do_not_change_copied_selector_identity():
    _, case, source, calls, selector, _, _ = await prepared()
    changed = {**selector, "maxTokens": 50000, "includeText": True}
    modify_messages(calls, "focused", request=changed)
    assert inspect(case, source, calls)["mechanicalNavigationSatisfied"]


@pytest.mark.parametrize(
    "failure", ["same_call", "only_local_outline", "failed_outline_input", "selected_before_outline"]
)
async def test_ordered_tool_calls_without_earlier_successful_model_delivery_do_not_prove_copying(failure):
    _, case, source, calls, _, _, _ = await prepared()
    if failure == "same_call":
        calls = [calls[-1]]  # All tool outputs first appear in one model input.
    elif failure == "only_local_outline":
        for call in calls:
            call["messages"] = [m for m in call["messages"] if not m.get("tool_call_id", "").startswith("outline")]
    elif failure == "failed_outline_input":
        for call in calls[:-1]:
            call["status"] = "failed"
    else:
        selected_request = calls[-1]["messages"][-2]
        for call in calls:
            call["messages"] = [
                copy.deepcopy(selected_request),
                *[m for m in call["messages"] if m != selected_request],
            ]
    result = inspect(case, source, calls)
    assert not result["copiedSelectorAfterEarlierModelDelivery"]
    assert not result["mechanicalNavigationSatisfied"]


async def test_outline_size_inspection_and_whole_document_recovery_cannot_pass():
    env, case, source, _, _, _, _ = await prepared()
    calls = []
    inspection = {"mode": "outline", "documents": [env.pin], "includeText": False}
    evidence = read(env, inspection)
    assert not evidence["documents"][0].get("outline")
    append_delivery(calls, inspection, evidence, "inspection")
    full = {"mode": "full", "documents": [env.pin]}
    append_delivery(calls, full, read(env, full), "fallback")
    result = inspect(case, source, calls)
    assert not result["nonemptyOutlineDelivered"] and result["fullReadRequested"]
    assert not result["mechanicalNavigationSatisfied"]


@pytest.mark.parametrize("change", ["scope", "extra_field", "pin", "bool_pin"])
async def test_selected_scope_or_pin_mutations_are_not_counted_as_copied_selectors(change):
    _, case, source, calls, selector, _, _ = await prepared()
    changed = copy.deepcopy(selector)
    if change == "scope":
        changed["documents"][0]["nodeIds"] = ["unadvertised"]
    elif change == "extra_field":
        changed["sectionLimit"] = 1
    elif change == "pin":
        changed["documents"][0]["contentRevision"] += 1
    else:
        changed["documents"][0]["generation"] = True
    modify_messages(calls, "focused", request=changed)
    observed = inspect(case, source, calls)
    assert not observed["copiedSelectorAfterEarlierModelDelivery"] and not observed["mechanicalNavigationSatisfied"]


@pytest.mark.parametrize(
    "change", ["missing_atom", "changed_text", "changed_location", "wrong_library", "wrong_source_hash", "incomplete"]
)
async def test_real_source_delivery_requires_complete_exact_current_publication(change):
    _, case, source, calls, _, evidence, _ = await prepared()
    altered = copy.deepcopy(evidence)
    document = altered["documents"][0]
    if change == "missing_atom":
        document["sections"].pop(0)
    elif change == "changed_text":
        document["sections"][-1]["text"] = "Changed rule."
    elif change == "changed_location":
        document["sections"][-1]["location"] = {"paragraphIndex": 999}
    elif change == "wrong_library":
        altered["library"]["libraryId"] = "foreign"
    elif change == "wrong_source_hash":
        document["sourceSha256"] = "0" * 64
    else:
        document["selectionComplete"] = False
    modify_messages(calls, "focused", evidence=altered)
    assert not inspect(case, source, calls)["mechanicalNavigationSatisfied"]


async def test_advertised_broad_ancestor_is_not_a_focused_source_read():
    env, case, source, calls, _, _, steps = await prepared()
    selector = steps[0][2]["subtreeSelector"]
    modify_messages(calls, "focused", request=selector, evidence=read(env, selector))
    result = inspect(case, source, calls)
    assert result["copiedSelectorAfterEarlierModelDelivery"]
    assert not result["focusedSourceDelivered"] and not result["mechanicalNavigationSatisfied"]


@pytest.mark.parametrize("include_text", ["omitted", True, 0, None, "false"])
async def test_full_content_or_nonboolean_false_like_request_fails_despite_focused_route(include_text):
    env, case, source, calls, _, _, _ = await prepared()
    request = {"documents": [env.pin]}
    if include_text != "omitted":
        request["includeText"] = include_text
    messages = copy.deepcopy(calls[-1]["messages"])
    messages.append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "unfulfilled-full",
                    "function": {
                        "name": "read_documents_library_reports",
                        "arguments": json.dumps(request),
                    },
                }
            ],
        }
    )  # Omitting mode defaults to full, even if no result was delivered.
    observed = inspect(case, source, calls, messages=messages)
    assert observed["focusedSourceDelivered"] and observed["fullReadRequested"]
    assert not observed["mechanicalNavigationSatisfied"]
    assert observed["fullInspectionRequestCount"] == 0


async def test_explicit_false_full_size_inspection_then_valid_outline_focused_route_can_pass():
    env, case, source, calls, _, _, _ = await prepared()
    request = {"mode": "full", "documents": [env.pin], "includeText": False}
    evidence = read(env, request)
    assert not evidence["documents"][0].get("sections")
    prefix = []
    append_delivery(prefix, request, evidence, "full-size-inspection")
    inspection_history = prefix[0]["messages"]
    for call in calls:
        call["logicalCall"] += 1
        call["messages"] = copy.deepcopy(inspection_history) + call["messages"]
    result = inspect(case, source, [*prefix, *calls])
    assert result["nonemptyOutlineDelivered"] and result["focusedSourceDelivered"]
    assert result["copiedSelectorAfterEarlierModelDelivery"] and result["mechanicalNavigationSatisfied"]
    assert not result["fullReadRequested"] and result["fullReadRequestIds"] == []
    assert result["fullInspectionRequestIds"] == ["full-size-inspection"]
    assert result["fullInspectionRequestCount"] == 1


async def test_answer_or_citation_claims_are_never_automatically_graded():
    _, case, source, calls, _, _, _ = await prepared()
    result = inspect(case, source, calls, answer="Incorrect rule, invented source page99.")
    assert result["mechanicalNavigationSatisfied"]
    assert result["sourceCitationReview"]["answerCorrect"] is None
    assert result["sourceCitationReview"]["citationCorrect"] is None
    assert result["manualReview"]["overallPass"] is None
    source[0]["sourceNavigation"]["sourceSha256"] = "0" * 64
    with pytest.raises(ValueError, match="publication differs"):
        inspect(case, source, calls)
