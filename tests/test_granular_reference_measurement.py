"""Measurements include real adapter overhead and preserve selected source scope."""

import copy
import io
import json
from types import SimpleNamespace

import pytest
from docx import Document

from fli.chunking import default_tokenizer
from fli.reference_format import pack
from scripts import granular_reference_measurement as measurement
from tests.test_responses_chat import message as answer_message
from tests.test_responses_chat import response


def evidence():
    return {
        "documents": [
            {
                "fileName": "source.docx",
                "outline": [{"title": "Exceptions", "navigationPreview": {"prefix": "Short useful preview."}}],
                "sections": [
                    {
                        "text": "Original first paragraph.",
                        "textSource": "extracted_source_text",
                        "readSelector": {
                            "mode": "selected",
                            "documents": [{"documentId": "example", "nodeIds": ["p1"]}],
                        },
                    },
                    {
                        "text": "Original second paragraph.",
                        "textSource": "extracted_source_text",
                        "readSelector": {
                            "mode": "selected",
                            "documents": [{"documentId": "example", "nodeIds": ["p2"]}],
                        },
                    },
                ],
            }
        ],
        "selectionComplete": True,
        "documentComplete": False,
    }


def message(payload, call_id="read-1"):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


@pytest.mark.parametrize("references", [False, True])
def test_source_and_preview_counters_survive_reference_columns_and_sum_only_in_declared_order(references):
    payload = evidence()
    wire = pack(payload) if references else payload
    result = measurement.measure_payload(wire)
    tokenizer = default_tokenizer()
    expected_source = sum(
        len(tokenizer.encode(text)) for text in ["Original first paragraph.", "Original second paragraph."]
    )
    assert result["renderedSourceTextTokensLocal"] == expected_source
    assert result["navigationPreviewTextTokensLocal"] == len(tokenizer.encode("Short useful preview."))
    assert sum(value for key, value in result.items() if key.startswith("ordered")) == result["evidenceJsonTokensLocal"]
    assert result["orderedRemainingNavigationProtocolTokensLocal"] > 0


def test_transcript_counts_actual_instructions_schemas_wrappers_and_repeated_exposures():
    system = {"role": "system", "content": "Use verified source evidence and preserve its scope."}
    user = {"role": "user", "content": "Read the paragraph."}
    tools = [{"type": "function", "function": {"name": "read", "description": "Read complete selected source."}}]
    first, second = message(evidence()), message(evidence(), "read-2")
    captures = [
        {"messages": [system, user], "tools": tools},
        {"messages": [system, user, first], "tools": tools},
        {"messages": [system, user, first, second], "tools": tools},
    ]
    result = measurement.measure_transcript(captures)
    assert result["uniqueToolReplies"] == 2 and result["toolReplyExposures"] == 3
    assert result["cumulativeSchemaJsonTokensLocal"] == 3 * measurement.count_json(tools)
    assert result["cumulativeMessagesAndToolsTokensLocal"] == sum(measurement.count_json(row) for row in captures)
    assert (
        result["uniqueToolReplyTotals"]["toolMessageJsonTokensLocal"]
        > result["uniqueToolReplyTotals"]["evidenceJsonTokensLocal"]
    )
    assert (
        result["cumulativeMessagesAndToolsTokensLocal"]
        > result["repeatedToolReplyExposureTotals"]["evidenceJsonTokensLocal"]
    )
    assert "no_provider_usage_measured" in result["counterStatus"]


def test_existing_references_counterfactual_preserves_all_evidence_and_does_not_mutate_input():
    captures = [
        {
            "messages": [{"role": "system", "content": "Original instructions"}, message(evidence())],
            "tools": [{"name": "unchanged"}],
        }
    ]
    before = copy.deepcopy(captures)
    result = measurement.compare_existing_references(captures)
    assert captures == before and result["exactExpandedEvidencePreserved"]
    assert (
        result["compact"]["cumulativeSchemaJsonTokensLocal"] == result["references"]["cumulativeSchemaJsonTokensLocal"]
    )
    assert (
        result["compact"]["uniqueToolReplyTotals"]["renderedSourceTextTokensLocal"]
        == result["references"]["uniqueToolReplyTotals"]["renderedSourceTextTokensLocal"]
    )


def test_fixture_is_repeatable_and_approximately_five_thousand_plain_source_tokens():
    first, description = measurement.source_fixture()
    second, again = measurement.source_fixture()
    assert first == second and description == again
    assert 5000 <= description["longSectionPlainSourceTokens"] <= 5200
    document = Document(io.BytesIO(first))
    assert any(paragraph.text == "Do not sample a damaged vessel." for paragraph in document.paragraphs)
    assert description["longSectionParagraphs"] >= 40


def test_visible_action_lookup_uses_human_context_and_supports_atomic_reference_union():
    hints = [{"title": "First", "navigationKind": "paragraph"}, {"title": "Second", "navigationKind": "paragraph"}]
    payload = {
        "documents": [{"outline": [{**hint, "actions": {"read": f"nav-safe-{n}"}} for n, hint in enumerate(hints)]}]
    }
    result = measurement._reference_argument([message(payload)], {"mode": "selected"}, "read", hints)
    assert result["references"] == ["nav-safe-0", "nav-safe-1"]
    assert "documents" not in result
    with pytest.raises(AssertionError, match="human label"):
        measurement._reference_argument([message(payload)], {"mode": "selected"}, "read", [{"title": "Missing"}])


async def test_bounded_real_api_agent_cases_preserve_model_facing_text_citations_scope_and_exact_requests():
    env, cases, fixture, _ = await measurement.prepare_local_cases()
    try:
        assert len(cases) == 9
        counts = {}
        for case in cases:
            baseline, baseline_requests = None, None
            for mode in ("compact", "references", "navigation"):
                result = measurement.run_case(env, case, mode)
                current = [(row["text"], row["citation"], row.get("sourceStructure")) for row in result["finalAtoms"]]
                requests = [measurement.omit(request, {"maxTokens"}) for request in result["actualRequests"]]
                if baseline is None:
                    baseline, baseline_requests = current, requests
                else:
                    assert current == baseline and requests == baseline_requests
                assert result["visibleFinalEvidence"]["documents"][0]["selectionComplete"] is True
                metric = measurement.measure_transcript(result["captures"])
                assert metric["uniqueToolReplyMeasurements"][-1]["renderedSourceTextTokensLocal"] > 0
                assert metric["cumulativeSchemaJsonTokensLocal"] > 0
                if mode == "navigation":
                    assert result["diagnostics"]["closed"] is True
                    uses = result["diagnostics"]["resolutions"]
                    assert all(
                        "reference" in row["originalArguments"] or "references" in row["originalArguments"]
                        for row in uses[1:]
                    )
                    if case["case"] == "delayed_within_turn":
                        assert uses[1]["originalArguments"]["reference"] == uses[3]["originalArguments"]["reference"]
                    if case["case"] == "multiple_paragraphs":
                        assert len(uses[-1]["originalArguments"]["references"]) == 2
                counts[case["case"]] = len(result["finalAtoms"])
        assert counts["small_paragraph"] == 1 and counts["multiple_paragraphs"] == 2
        assert counts["complete_cell"] == 4 and counts["complete_table"] == 7
        assert counts["authored_section"] == 2 and counts["authored_subtree"] == 11
        assert counts["source_approximately_5000_tokens"] == fixture["longSectionParagraphs"] + 1
    finally:
        env.client.close()


async def test_invalid_measurement_profile_refuses_before_creating_output(tmp_path):
    output = tmp_path / "measurements"
    with pytest.raises(ValueError, match="unknown"):
        await measurement.measure(("not-a-presentation",), private_output=output)
    assert not output.exists()


def test_retained_baseline_replay_is_read_only_and_refuses_wire_drift(tmp_path):
    root = tmp_path / "retained"
    payload = evidence()
    payload["documents"][0]["sections"][0]["navigationNodeId"] = "p1"
    payload["documents"][0]["sections"][1]["navigationNodeId"] = "p2"
    messages = [
        {"role": "system", "content": "Read only source evidence."},
        {"role": "user", "content": "Read the selected source."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "read-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
        },
        message(payload),
    ]
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    raw = response(answer_message("Verified source delivered."))
    requests = []

    def create(**request):
        requests.append(request)
        return raw

    adapter = measurement.azure_responses_chat(
        "offline",
        "fixed",
        client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        reasoning_effort="medium",
        max_output_tokens=8000,
    )
    adapter(messages, tools)
    files = {
        "models/controls.json": {"cases": [{"case": "example"}]},
        "models/status.json": {"status": "complete_pending_source_review"},
        "models/started.json": {"deployment": "fixed"},
        "models/provider/ledger.json": {
            "logicalCallsStarted": 1,
            "physicalResponsesAttempts": 1,
            "unmeteredAttempts": 0,
            "knownInputTokens": 100,
            "knownOutputTokens": 50,
        },
        "models/provider/request-00001.json": requests[0],
        "models/provider/response-00001.json": raw,
        "models/example/model-input-01.json": {"messages": messages, "tools": tools},
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    before = {name: (root / name).read_bytes() for name in files}
    report = measurement.measure_retained(root)
    assert report["normalResponsesWireRequestsMatched"] == 1
    assert report["priorFilesVerifiedUnchanged"] == len(files)
    assert report["retainedProviderUsage"] == {"inputTokens": 100, "outputTokens": 50}
    assert report["newCloudCalls"] == report["newModelCalls"] == 0
    assert before == {name: (root / name).read_bytes() for name in files}
    changed = copy.deepcopy(requests[0])
    changed["store"] = True
    (root / "models/provider/request-00001.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="differs from retained wire"):
        measurement.measure_retained(root)


def test_retained_partial_and_empty_roots_are_not_reported_as_completed(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ValueError, match="empty"):
        measurement.measure_retained(root)
    (root / "models").mkdir()
    (root / "models/controls.json").write_text(json.dumps({"cases": [{"case": "example"}]}))
    (root / "models/status.json").write_text(json.dumps({"status": "running"}))
    with pytest.raises(ValueError, match="completed retained"):
        measurement.measure_retained(root)
