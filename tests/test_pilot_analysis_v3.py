"""Public synthetic regressions for the versioned offline delivery analysis.

All source text, provider payloads, accepted receipts and paths below are generated
fixtures. No live provider, captured experiment, credential or private corpus is read.
"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fli.chunking import default_tokenizer

HELPERS = Path(__file__).resolve().parents[1] / "tools" / "pilot_analysis"
spec = importlib.util.spec_from_file_location("pilot_analysis_v3_tested", HELPERS / "analysis_v3.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def metrics(prompt=56000):
    return {
        "purpose": "tool_request",
        "finishReason": "tool_calls",
        "api": "responses",
        "responseStatus": "completed",
        "promptTokens": prompt,
        "completionTokens": 300,
        "contextCarryoverTokensEstimate": 200,
    }


def fixture(*, candidate=False):
    delivered = {
        "mode": "evidence_search",
        "results": [
            {"fileName": "example.docx", "text": "Synthetic source: notice is required.", "location": {"paragraph": 1}}
        ],
    }
    tail = {
        "disposition": "candidates_found" if candidate else "budget_exceeded",
        "documents": [{"documentId": "doc-a", "complete": False}, {"documentId": "doc-b", "complete": False}]
        if candidate
        else [],
        "error": "remaining_context_budget_exceeded",
        "complete": False,
        "message": "No content delivered. Narrow the selection or disclose incomplete coverage.",
        "progress": {
            "newExtents": 2 if candidate else 0,
            "repeatedExtents": 0,
            "consecutiveNoProgress": 0 if candidate else 1,
            "recoveryRequired": False,
        },
        "nativeImageDelivery": {
            "displayAvailable": [],
            "requested": [],
            "supplied": [],
            "alreadySupplied": [],
            "withheld": [],
            "reason": None,
        },
    }
    function = {
        "type": "function_call",
        "id": "function-next",
        "call_id": "call-next",
        "name": "find_documents_library_fixture" if candidate else "search_library_fixture",
        "arguments": json.dumps(
            {"mode": "semantic_candidates", "query": "notice"} if candidate else {"query": "notice"}
        ),
        "status": "completed",
    }
    initial = [
        {"role": "system", "content": "Answer using the synthetic source."},
        {"role": "user", "content": "What does the fictional schedule require?"},
    ]
    previous = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-first",
                "type": "function",
                "function": {"name": "search_library_fixture", "arguments": '{"query":"notice"}'},
            }
        ],
    }
    messages = initial + [previous, {"role": "tool", "tool_call_id": "call-first", "content": json.dumps(delivered)}]
    calls = [{"messages": initial, "tools": []}, {"messages": messages, "tools": []}]
    request = {"input": [{"type": "function_call_output", "call_id": "call-first", "output": json.dumps(delivered)}]}
    response = {
        "status": "completed",
        "output": [{"type": "reasoning", "id": "reason-next", "encrypted_content": "synthetic-opaque-item"}, function],
        "usage": {"input_tokens": 56000, "output_tokens": 300, "output_tokens_details": {"reasoning_tokens": 200}},
    }
    trace = {
        "tool_name": function["name"],
        "operation": "discover" if candidate else "search",
        "disposition": tail["disposition"],
        "result_count": 0,
        "error": None,
        "notes": ["Tool content withheld because the remaining model context budget was exceeded."],
    }
    result = {
        "case": "synthetic-question",
        "question": initial[1]["content"],
        "answer": a.PRE_CALL,
        "toolReplies": [delivered, tail],
        "metrics": {"modelCalls": [metrics(2000), metrics()]},
        "traces": [trace],
    }
    return result, calls, request, response


def prove(values, **kwargs):
    return a.prove_local_stop(
        *values,
        tokenizer=default_tokenizer(),
        grounding_state=lambda q, m: (True, True, []),
        source_document_ids={"doc-a", "doc-b"},
        **kwargs,
    )


def expected_guard(values, *, synthesis=False):
    """Construct the independently expected agent transcript after its returned call."""
    result, calls, _, response = values
    function = response["output"][1]
    original = calls[-1]["messages"]
    after = original + [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": function["call_id"],
                    "type": "function",
                    "function": {"name": function["name"], "arguments": function["arguments"]},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": function["call_id"],
            "content": json.dumps(result["toolReplies"][-1], ensure_ascii=False, separators=(",", ":")),
        },
    ]
    if synthesis:
        after.append({"role": "user", "content": a.TOOL_LIMIT})
    tokenizer = default_tokenizer()
    before_count = len(tokenizer.encode(json.dumps({"messages": original, "tools": []})))
    after_count = len(tokenizer.encode(json.dumps({"messages": after, "tools": []})))
    return response["usage"]["input_tokens"] + after_count - before_count + 200 + 8000


def test_exact_provider_prefix_is_returned_without_mutating_local_history():
    values = fixture()
    before = copy.deepcopy(values)
    delivered, proof = prove(values)
    assert values == before
    assert delivered == [values[0]["toolReplies"][0]]
    assert proof["localReplyCount"] == 2 and proof["providerReceivedReplyCount"] == 1
    assert proof["unsubmittedReplyCount"] == 1 and proof["unsubmittedEvidenceCredited"] is False
    assert proof["reconstructedGuardTotal"] == expected_guard(values) > 64000


def test_guard_boundary_rejects_equal_budget_and_accepts_one_token_over():
    values = fixture()
    delta = expected_guard(values) - values[3]["usage"]["input_tokens"]
    boundary_input = 64000 - delta
    values[3]["usage"]["input_tokens"] = values[0]["metrics"]["modelCalls"][-1]["promptTokens"] = boundary_input
    with pytest.raises(ValueError, match="guard would not stop"):
        prove(values)
    values[3]["usage"]["input_tokens"] += 1
    values[0]["metrics"]["modelCalls"][-1]["promptTokens"] += 1
    assert prove(values)[1]["reconstructedGuardTotal"] == 64001


@pytest.mark.parametrize(
    "change,match",
    [
        ("local_prefix", "exact provider prefix"),
        ("physical_payload", "physical request differs"),
        ("invented_answer", "exact frozen local guard"),
        ("unknown_usage", "provider usage differs"),
        ("missing_reasoning", "reasoning accounting"),
        ("answer_call", "completed tool request"),
        ("failed_response", "not complete"),
        ("extra_function", "count/identity"),
    ],
)
def test_incomplete_or_forged_provenance_is_rejected(change, match):
    values = fixture()
    row, _, request, response = values
    if change == "local_prefix":
        row["toolReplies"][0] = {"forged": True}
    elif change == "physical_payload":
        request["input"][0]["output"] = "{}"
    elif change == "invented_answer":
        row["answer"] = "An unsupported claim about the source."
    elif change == "unknown_usage":
        response["usage"]["input_tokens"] += 1
    elif change == "missing_reasoning":
        response["output"][0].pop("encrypted_content")
    elif change == "answer_call":
        row["metrics"]["modelCalls"][-1]["purpose"] = "answer"
    elif change == "failed_response":
        response["status"] = "failed"
    else:
        response["output"].append({**response["output"][1], "call_id": "extra-call"})
    with pytest.raises(ValueError, match=match):
        prove(values)


@pytest.mark.parametrize(
    "change", ["source_body", "document_text", "image", "unexpected_metadata", "unknown_disposition"]
)
def test_unsubmitted_source_body_images_or_other_tail_forms_never_receive_credit(change):
    values = fixture()
    tail = values[0]["toolReplies"][-1]
    if change == "source_body":
        tail["results"] = [{"fileName": "unsubmitted.docx", "text": "This text was never submitted." * 100}]
    elif change == "document_text":
        tail["documents"] = [{"documentId": "doc-a", "complete": False, "text": "Unsubmitted source text"}]
    elif change == "image":
        tail["nativeImageDelivery"]["supplied"] = ["unsubmitted-image"]
    elif change == "unexpected_metadata":
        tail["progress"]["description"] = "A generated claim is not source evidence."
    else:
        tail["disposition"] = "new_unproved_kind"
    with pytest.raises(ValueError, match="unsubmitted|native-image"):
        prove(values)


def test_discovery_markers_can_be_new_extents_without_becoming_delivered_source():
    values = fixture(candidate=True)
    delivered, proof = prove(values)
    assert values[0]["toolReplies"][-1]["progress"]["newExtents"] == 2
    assert proof["unsubmittedDispositions"] == ["candidates_found"]
    assert proof["reconstructedGuardTotal"] == expected_guard(values)
    assert delivered == [values[0]["toolReplies"][0]]


@pytest.mark.parametrize(
    "change",
    ["tool_name", "mode", "operation", "trace_name", "trace_disposition", "note", "foreign_id", "duplicate_id"],
)
def test_candidate_refusal_requires_matching_actual_discovery_and_source_identity(change):
    values = fixture(candidate=True)
    row, _, _, response = values
    trace, function = row["traces"][-1], response["output"][1]
    if change == "tool_name":
        function["name"] = "search_library_fixture"
    elif change == "mode":
        function["arguments"] = '{"mode":"catalogue"}'
    elif change == "operation":
        trace["operation"] = "search"
    elif change == "trace_name":
        trace["tool_name"] = "different-tool"
    elif change == "trace_disposition":
        trace["disposition"] = "results"
    elif change == "note":
        trace["notes"] = []
    elif change == "foreign_id":
        row["toolReplies"][-1]["documents"][0]["documentId"] = "foreign-document"
    else:
        row["toolReplies"][-1]["documents"][1]["documentId"] = "doc-a"
    with pytest.raises(ValueError, match="candidate"):
        prove(values)


def test_twelfth_round_guard_uses_the_existing_synthesis_outcome():
    values = fixture()
    values[0]["metrics"]["modelCalls"] = [metrics()] * 12
    values[0]["answer"] = a.SYNTHESIS
    proof = prove(values)[1]
    assert proof["branch"] == "post-tool-round-context-guard"
    assert proof["reconstructedGuardTotal"] == expected_guard(values, synthesis=True)


def test_normal_path_and_unrelated_failures_are_never_reinterpreted():
    result, delivered, receipts = {"toolReplies": ["original"]}, ["original"], [1]

    def normal(root, member):
        return result, delivered, receipts

    value, proof = a.inspected_wire(None, normal, None, None)
    assert value[0] is result and value[1] is delivered and value[2] is receipts
    assert proof["originalVerifierPassed"] is True

    def unrelated(root, member):
        raise ValueError("wire hash changed")

    with pytest.raises(ValueError, match="unrecognized original verifier"):
        a.inspected_wire(None, unrelated, None, None)


def synthetic_prior_batch(tmp_path, monkeypatch):
    """Pin a generated approval hash; exercise the real, byte-pinned v2 helper."""
    root, batch = tmp_path / "run", tmp_path / "batch"
    root.mkdir()
    batch.mkdir()
    controls = {"codeSha256": {"synthetic-producer.py": "test-code-digest"}}
    write(root / "controls.json", controls)
    member = {"reviewId": "opaque-generated-review", "case": "synthetic-question", "run": "synthetic-run"}
    write(root / "blind-map.json", [member])
    prior_helper = HELPERS / "analysis_v2.py"
    assert a.sha(prior_helper) == a.PRIOR_APPROVED["sourceSha256"]
    approval = {
        "schema": "fli.hybrid-model-pilot.analysis-correction-acceptance.v2",
        "status": "accepted",
        "analysisVersion": a.PRIOR_APPROVED["version"],
        "analysisSourceSha256": a.sha(prior_helper),
        "frozenControlsSha256": a.sha(root / "controls.json"),
        "producerCodeSha256": controls["codeSha256"],
        **dict.fromkeys(a.ACCEPTANCE_FLAGS, True),
    }
    approved_path = tmp_path / "generated-approval.json"
    write(approved_path, approval)
    pinned = {**a.PRIOR_APPROVED, "acceptanceSha256": a.sha(approved_path)}
    monkeypatch.setattr(a, "PRIOR_APPROVED", pinned)
    receipt = {
        "schema": "fli.private.hybrid-model-pilot.review-batch.v1",
        "producerSha256": a.BATCH_HELPER_SHA,
        "sourceRun": str(root),
        "analysisBinding": pinned,
        "packets": [{"reviewId": member["reviewId"], "case": member["case"]}],
    }
    write(batch / "batch-receipt.json", receipt)
    checked = []

    def original(directory, selected):
        checked.append((directory, selected))
        return {"toolReplies": []}, [], []

    p = SimpleNamespace(
        private_path=lambda v: Path(v).resolve(),
        digest=digest,
        h=SimpleNamespace(wire_result=original, hashes=lambda: controls["codeSha256"]),
    )
    return p, root, batch, approved_path, prior_helper, member, checked


def test_pinned_prior_v2_batch_revalidates_approval_and_every_member_without_mutation(tmp_path, monkeypatch):
    p, root, batch, approved, helper, member, checked = synthetic_prior_batch(tmp_path, monkeypatch)
    before = (batch / "batch-receipt.json").read_bytes()
    assert a.validate_batch_binding(p, root, batch, approved, helper, approved) == "prior-accepted-v2-unchanged"
    assert checked == [(root, member)] and (batch / "batch-receipt.json").read_bytes() == before


@pytest.mark.parametrize(
    "change", ["approval_bytes", "helper_bytes", "unknown_binding", "null_binding", "missing_files"]
)
def test_prior_v2_compatibility_rejects_unpinned_or_missing_artifacts(tmp_path, monkeypatch, change):
    p, root, batch, approved, helper, _, _ = synthetic_prior_batch(tmp_path, monkeypatch)
    if change == "approval_bytes":
        approved.write_text("{}")
    elif change == "helper_bytes":
        altered = tmp_path / "altered_helper.py"
        altered.write_bytes(helper.read_bytes() + b"\n# altered\n")
        helper = altered
    elif change in {"unknown_binding", "null_binding"}:
        receipt = a.read(batch / "batch-receipt.json")
        receipt["analysisBinding"] = None if change == "null_binding" else {"version": "unaccepted"}
        write(batch / "batch-receipt.json", receipt)
    else:
        helper = None
    with pytest.raises(ValueError, match="another analysis|helper/acceptance bytes required"):
        a.validate_batch_binding(p, root, batch, approved, helper, approved)


def test_prior_acceptance_must_be_valid_even_if_its_hash_was_pinned(tmp_path, monkeypatch):
    p, root, batch, approved, helper, _, _ = synthetic_prior_batch(tmp_path, monkeypatch)
    approval = a.read(approved)
    approval["status"] = "unreviewed"
    write(approved, approval)
    pinned = {**a.PRIOR_APPROVED, "acceptanceSha256": a.sha(approved)}
    monkeypatch.setattr(a, "PRIOR_APPROVED", pinned)
    receipt = a.read(batch / "batch-receipt.json")
    receipt["analysisBinding"] = pinned
    write(batch / "batch-receipt.json", receipt)
    with pytest.raises(ValueError, match="corrective-analysis acceptance differs"):
        a.validate_batch_binding(p, root, batch, approved, helper, approved)


def test_a_prior_v2_label_cannot_authorize_the_new_candidate_class(tmp_path, monkeypatch):
    p, root, batch, approved, helper, member, _ = synthetic_prior_batch(tmp_path, monkeypatch)
    values = fixture(candidate=True)
    row, calls, request, response = values
    write(root / member["run"] / "results.json", [row])
    receipts = []
    for i, call in enumerate(calls, 1):
        write(root / ("wire-" + member["run"]) / f"wire-{i:03}.json", call)
        receipts.append({"call": i, "wireSha256": digest(call["messages"]), "referenceToolMessages": 0})
    write(root / ("wire-" + member["run"]) / "wire-receipts.json", receipts)
    write(
        root / "provider/ledger.json",
        {"calls": [{"run": member["run"], "status": "response", "attempt": i, "logicalCall": i} for i in (1, 2)]},
    )
    write(root / "provider/request-00002.json", request)
    write(root / "provider/response-00002.json", response)

    def original(*args):
        raise ValueError(a.OLD_FAILURE)

    p.h.wire_result = original
    with pytest.raises(ValueError, match="proven no-content refusal class"):
        a.validate_batch_binding(p, root, batch, approved, helper, approved)


def test_unbound_original_batch_requires_original_verifier_success(tmp_path, monkeypatch):
    p, root, batch, approved, _, member, checked = synthetic_prior_batch(tmp_path, monkeypatch)
    receipt = a.read(batch / "batch-receipt.json")
    del receipt["analysisBinding"]
    write(batch / "batch-receipt.json", receipt)
    assert a.validate_batch_binding(p, root, batch, approved) == "original-v1-normal-path-unchanged"
    assert checked == [(root, member)]

    def original(*args):
        raise ValueError(a.OLD_FAILURE)

    p.h.wire_result = original
    with pytest.raises(ValueError, match=a.OLD_FAILURE):
        a.validate_batch_binding(p, root, batch, approved)
