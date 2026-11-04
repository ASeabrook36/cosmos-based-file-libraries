"""Two sealed followup cases and source-based observations; no model/cloud calls."""

import copy
import json

import pytest

from scripts import granular_followup_cases as followup
from scripts.granular_live_models import cases as original_cases
from scripts.qualify_granular_navigation import fixtures
from tests.test_granular_live_models import source_bindings
from tests.test_granular_navigation import read, ready


def successful_call(selector, evidence, *, identity="call_read"):
    return {
        "status": "returned",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": identity,
                        "function": {"name": "read_documents_library_reports", "arguments": json.dumps(selector)},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": identity, "content": json.dumps(evidence)},
        ],
    }


async def prepared():
    env = await ready(source=fixtures()["source.docx"], name="source.docx")
    binding = {
        "libraryId": env.lib,
        "documentId": env.doc["id"],
        "generation": env.doc["activeGeneration"],
        "contentRevision": env.doc["contentRevision"],
        "fileName": env.doc["fileName"],
        "sourceSha256": env.doc["sha256"],
    }
    sections = json.loads(await env.s.blobs.download(env.doc["fullText"]["blobPath"]))
    return env, followup.cases({"docx": binding}), sections


def test_two_deterministic_cases_keep_gold_out_of_prompts_and_cell_prompt_unchanged():
    bindings = source_bindings()
    cases = followup.cases({"docx": bindings["docx"]})
    assert [c["case"] for c in cases] == ["cell-completeness", "source-identifier-fields"]
    old = next(c for c in original_cases(bindings) if c["case"] == "cell-qualifier")
    assert cases[0]["question"] == old["question"]
    assert "JSON object" in cases[1]["question"]
    assert "state whether a rendered page number or authored chapter number is available" in cases[1]["question"]
    assert "Explicitly disclose unavailable" in cases[1]["reviewCriteria"]["sourceAnswer"]
    assert all(fact not in case["question"] for case in cases for fact in case["expectedSourceFacts"])
    assert all("ev1." not in case["question"] and '"h5"' not in case["question"] for case in cases)
    assert all(case["reviewCriteria"] and case["requiredCitationFile"] == "source.docx" for case in cases)
    cases[0]["sourceBinding"]["generation"] = 50
    cases[0]["expectedSourceFacts"].clear()
    assert followup.cases({"docx": bindings["docx"]})[0]["sourceBinding"]["generation"] == 1
    assert followup.cases({"docx": bindings["docx"]})[0]["expectedSourceFacts"] == followup.CELL_FACTS


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", True),
        ("generation", 0),
        ("contentRevision", None),
        ("sourceSha256", "unknown"),
        ("fileName", "source.md"),
    ],
)
def test_invalid_source_binding_refuses_before_any_execution(field, value):
    binding = source_bindings()["docx"]
    binding[field] = value
    with pytest.raises(ValueError):
        followup.cases({"docx": binding})
    with pytest.raises(ValueError, match="DOCX-only"):
        followup.cases(source_bindings())


async def test_actual_cell_node_receipt_counts_but_bulk_paragraph_selection_does_not():
    env, cases, source = await prepared()
    full = read(env)
    parts = [
        s
        for s in full["documents"][0]["sections"]
        if s["location"]["originalCoordinate"].get("row") == 1
        and s["location"]["originalCoordinate"].get("column") == 1
    ]
    selector = parts[0]["parentSelector"]
    cell = read(env, selector)
    observations = followup.inspect_turn(
        cases[0], {"answer": "manual review required"}, [successful_call(selector, cell)], source
    )
    assert observations["completeCellReadDelivered"] and len(observations["expectedSourceAtoms"]) == 4
    assert observations["manualReview"]["overallPass"] is None
    bulk = {"mode": "selected", "documents": [{**env.pin, "sectionIds": [p["sectionId"] for p in parts]}]}
    bulk_result = read(env, bulk)
    assert len(bulk_result["documents"][0]["sections"]) == 4
    rejected = followup.inspect_turn(
        cases[0], {"answer": "all source facts correct"}, [successful_call(bulk, bulk_result)], source
    )
    assert rejected["completeCellReadDelivered"] is False


@pytest.mark.parametrize("failure", ["unsent", "partial", "duplicate", "changed_text", "wrong_pin"])
async def test_cell_observation_requires_actual_successful_model_delivery_and_exact_source(failure):
    env, cases, source = await prepared()
    paragraph = next(s for s in read(env)["documents"][0]["sections"] if followup.CELL_FACTS[0] in s["text"])
    selector = paragraph["parentSelector"]
    evidence = read(env, selector)
    call = successful_call(selector, evidence)
    if failure == "unsent":
        call["status"] = "failed"
    else:
        if failure == "partial":
            evidence["documents"][0]["sections"].pop()
        elif failure == "duplicate":
            evidence["documents"][0]["sections"].append(copy.deepcopy(evidence["documents"][0]["sections"][0]))
        elif failure == "changed_text":
            evidence["documents"][0]["sections"][0]["text"] = "Changed text"
        elif failure == "wrong_pin":
            selector["documents"][0]["generation"] += 1
        call = successful_call(selector, evidence)
    assert not followup.inspect_turn(cases[0], {"answer": "correct-looking text"}, [call], source)[
        "completeCellReadDelivered"
    ]


@pytest.mark.parametrize("answer_form", ["correct_json", "swapped_ids", "wrong_citation", "contradictory_prose"])
async def test_exact_identifier_checks_use_delivered_paragraph_but_leave_semantic_judgment_manual(answer_form):
    env, cases, source = await prepared()
    paragraph = next(s for s in read(env)["documents"][0]["sections"] if followup.RULE in s["text"])
    selector = paragraph["readSelector"]
    evidence = read(env, selector)
    outline_selector = {"mode": "outline", "documents": [env.pin]}
    calls = [
        successful_call(outline_selector, read(env, outline_selector), identity="outline"),
        successful_call(selector, evidence),
    ]
    fields = {k: paragraph[k] for k in ("sectionId", "containingSectionId", "citation")}
    if answer_form == "swapped_ids":
        fields["sectionId"], fields["containingSectionId"] = fields["containingSectionId"], fields["sectionId"]
    elif answer_form == "wrong_citation":
        fields["citation"] = "invented page 99"
    answer = "```json\n" + json.dumps(fields, indent=2) + "\n```"
    if answer_form == "contradictory_prose":
        answer += " The containing-section ID is the evidence handle."
    observed = followup.inspect_turn(cases[1], {"answer": answer}, calls, source)
    assert observed["sourceParagraphDelivered"] and observed["sourceParagraphTextMatches"]
    assert observed["outlineBeforeSourceRead"] and observed["returnedAuthoredContainerMatchesSource"]
    assert observed["exactReportedFields"] == (answer_form in {"correct_json", "contradictory_prose"})
    assert observed["manualReview"]["overallPass"] is None
    assert all(row["passed"] is None for row in observed["manualReview"]["criteria"].values())
    source[0]["sourceNavigation"]["sourceSha256"] = "0" * 64
    with pytest.raises(ValueError, match="publication differs"):
        followup.inspect_turn(cases[1], {"answer": answer}, calls, source)


async def test_outline_inspections_followed_by_full_source_do_not_count_as_delivered_authored_outline():
    env, cases, source = await prepared()
    inspection = {"mode": "outline", "documents": [env.pin], "includeText": False}
    manifest = read(env, inspection)
    assert manifest["disposition"] == "inspected" and not manifest["documents"][0].get("outline")
    full_selector = {"mode": "full", "documents": [env.pin]}
    full = read(env, full_selector)
    paragraph = next(s for s in full["documents"][0]["sections"] if followup.RULE in s["text"])
    answer = json.dumps({key: paragraph[key] for key in ("sectionId", "containingSectionId", "citation")})
    calls = [
        successful_call(inspection, manifest, identity="inspection"),
        successful_call(full_selector, full, identity="full"),
    ]
    observed = followup.inspect_turn(cases[1], {"answer": answer}, calls, source)
    assert observed["sourceParagraphDelivered"] and observed["sourceParagraphTextMatches"]
    assert observed["exactReportedFields"]
    assert observed["outlineBeforeSourceRead"] is False
    assert observed["manualReview"]["overallPass"] is None
