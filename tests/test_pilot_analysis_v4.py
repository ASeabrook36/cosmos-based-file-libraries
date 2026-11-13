"""Synthetic exact-case approval tests: no captured provider or private corpus inputs."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_pilot_analysis_v3 import HELPERS, digest, fixture, write

from fli.chunking import default_tokenizer

spec = importlib.util.spec_from_file_location("pilot_analysis_v4_tested", HELPERS / "analysis_v4.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def source_fixture():
    result, calls, request, response = fixture()
    selector = {"documentId": "fictional-document", "generation": 1, "contentRevision": 2}
    native = {
        "displayAvailable": [],
        "requested": [],
        "supplied": [],
        "alreadySupplied": [],
        "withheld": [],
        "reason": None,
    }
    outline = {
        "mode": "outline",
        "disposition": "outline",
        "documents": [
            {"documentSelector": selector, "outline": [{"sectionId": "fictional-section", "sectionTokens": 30000}]}
        ],
        "progress": {"newExtents": 2, "repeatedExtents": 0, "consecutiveNoProgress": 0, "recoveryRequired": False},
        "nativeImageDelivery": copy.deepcopy(native),
    }
    # Quoted source text exercises the extra JSON escaping of enclosing tool messages.
    evidence = {
        "mode": "evidence_search",
        "disposition": "results",
        "filtersApplied": {"tags": ["topic:fiction"]},
        "results": [
            {
                "documentId": "fictional-document",
                "fileName": "fiction.docx",
                "evidenceId": f"piece-{i}",
                "text": ('Fictional notice: "review"; path: \\archive\\file.\n' * 400),
            }
            for i in range(3)
        ],
        "passagesMatched": 3,
        "passagesDelivered": 3,
        "contextLimited": False,
        "progress": {"newExtents": 3, "repeatedExtents": 0, "consecutiveNoProgress": 0, "recoveryRequired": False},
        "nativeImageDelivery": copy.deepcopy(native),
    }
    functions = [
        {
            "type": "function_call",
            "id": "outline-response",
            "call_id": "outline-call",
            "name": "read_documents_library_fixture",
            "arguments": json.dumps({"mode": "outline", "documents": [selector]}),
            "status": "completed",
        },
        {
            "type": "function_call",
            "id": "search-response",
            "call_id": "search-call",
            "name": "search_library_fixture",
            "arguments": json.dumps({"query": "fictional notice", "tags": ["topic:fiction"]}),
            "status": "completed",
        },
    ]
    response["output"] = functions
    response["usage"]["output_tokens_details"]["reasoning_tokens"] = 0
    result["metrics"]["modelCalls"][-1]["contextCarryoverTokensEstimate"] = 0
    result["toolReplies"] = [result["toolReplies"][0], outline, evidence]
    result["traces"] = [
        {"tool_name": functions[0]["name"], "operation": "read", "disposition": "outline", "error": None, "notes": []},
        {
            "tool_name": functions[1]["name"],
            "operation": "search",
            "disposition": "results",
            "error": None,
            "notes": [],
            "filters": {"tags": ["topic:fiction"]},
            "filters_applied": {"tags": ["topic:fiction"]},
        },
    ]
    tokenizer = default_tokenizer()
    wire = calls[-1]

    def size(messages):
        return len(tokenizer.encode(json.dumps({"messages": messages, "tools": wire["tools"]})))

    following = copy.deepcopy(wire["messages"])
    following.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": f["call_id"], "type": "function", "function": {"name": f["name"], "arguments": f["arguments"]}}
                for f in functions
            ],
        }
    )
    before_tools = []
    for function, payload in zip(functions, (outline, evidence), strict=True):
        before_tools.append(size(following))
        following.append(
            {
                "role": "tool",
                "tool_call_id": function["call_id"],
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        )
    # Place the next guard exactly one unit beyond the fixed allowance, using
    # wholly synthetic usage. Both pre-annotation tool bodies still fit.
    prompt = 64001 - 8000 - (size(following) - size(wire["messages"]))
    assert prompt > 0
    response["usage"]["input_tokens"] = prompt
    result["metrics"]["modelCalls"][-1]["promptTokens"] = prompt
    budgets = []
    for before, payload in zip(before_tools, (outline, evidence), strict=True):
        remaining = max(0, 64000 - prompt - max(0, before - size(wire["messages"])) - 8512)
        base = {k: v for k, v in payload.items() if k not in {"progress", "nativeImageDelivery"}}
        compact = len(tokenizer.encode(json.dumps(base, ensure_ascii=False, separators=(",", ":"))))
        spaced = len(tokenizer.encode(json.dumps(base, ensure_ascii=False)))
        assert compact <= remaining and spaced <= remaining
        budgets.append({"remaining": remaining, "baseCompactTokens": compact, "baseSearchSerializationTokens": spaced})
    proof = {
        "proofSha256": "generated-proof",
        "independentReviewSha256": "generated-review",
        "unsubmittedEvidenceCredited": False,
        "perToolBudgets": budgets,
        "reconstructedGuardTotal": 64001,
    }
    return (result, calls, request, response), proof


def prove(values, proof):
    return a.prove_local_stop(
        *values, tokenizer=default_tokenizer(), grounding_state=lambda *_: (True, True, []), exact_source_proof=proof
    )


def test_source_bodies_remain_excluded_and_raw_bytes_unchanged_after_exact_guard_proof():
    values, proof = source_fixture()
    original = copy.deepcopy(values)
    actual, details = prove(values, proof)
    assert values == original
    assert actual == values[0]["toolReplies"][:1]
    assert details["providerReceivedReplyCount"] == 1 and details["unsubmittedReplyCount"] == 2
    assert details["reconstructedGuardTotal"] == 64001 and details["unsubmittedEvidenceCredited"] is False
    with pytest.raises(ValueError, match="proven no-content refusal"):
        prove(values, None)


@pytest.mark.parametrize("change", ["body", "image", "call", "scope", "filter", "trace", "guard", "budget"])
def test_approved_shape_does_not_bypass_local_semantics_or_guard(change):
    values, proof = source_fixture()
    if change == "body":
        values[0]["toolReplies"][-1]["results"][0]["text"] += " changed serialization"
    elif change == "image":
        values[0]["toolReplies"][-1]["nativeImageDelivery"]["supplied"] = ["image"]
    elif change == "call":
        values[3]["output"][1]["name"] = "unexpected_tool"
    elif change == "scope":
        values[0]["toolReplies"][-2]["documents"][0]["documentSelector"] = {}
    elif change == "filter":
        values[0]["traces"][-1]["filters"] = {}
    elif change == "trace":
        values[0]["traces"][-1]["error"] = "failed"
    elif change == "guard":
        proof["reconstructedGuardTotal"] += 1
    else:
        proof["perToolBudgets"][0]["remaining"] += 1
    with pytest.raises(ValueError):
        prove(values, proof)


def bound_fixture(tmp_path, monkeypatch):
    values, detail = source_fixture()
    result, calls, request, response = values
    member = {"run": "fictional-cell", "case": "fictional-question", "cell": "sol-hybrid"}
    root = tmp_path / "run"
    root.mkdir()
    attempts = [{"run": member["run"], "attempt": i, "status": "response", "logicalCall": i} for i in (1, 2)]
    paths = {
        member["run"] + "/results.json": [result],
        "source-snapshot.json": {"fictional": True},
        "wire-" + member["run"] + "/wire-receipts.json": [
            {"call": i, "wireSha256": digest(c["messages"]), "referenceToolMessages": 0} for i, c in enumerate(calls, 1)
        ],
    }
    paths.update({"wire-" + member["run"] + f"/wire-{i:03}.json": c for i, c in enumerate(calls, 1)})
    paths.update({f"provider/request-{i:05}.json": request for i in (1, 2)})
    paths.update({f"provider/response-{i:05}.json": response for i in (1, 2)})
    for name, value in paths.items():
        write(root / name, value)
    write(root / "controls.json", {"codeSha256": {"synthetic": "producer"}})
    source = tmp_path / "source.json"
    write(source, {"text": "Fictional immutable source artifact"})
    review = tmp_path / "review.json"
    review_value = {
        "schema": "fli.hybrid-model-pilot.source-tail-independent-review.v1",
        "status": "accepted",
        **member,
        **dict.fromkeys(a.SOURCE_REVIEW_FLAGS, True),
        "artifactBindings": [{"path": str(source), "sha256": a.sha(source)}]
        + [{"path": str(root / name), "sha256": a.sha(root / name)} for name in [*paths, "controls.json"]],
        "details": {
            "reconstructedGuardTotal": detail["reconstructedGuardTotal"],
            "perToolBudget": [
                {
                    "remainingBeforeTool": b["remaining"],
                    "compactPayloadBeforeAnnotations": b["baseCompactTokens"],
                    "spacedPayloadBeforeAnnotations": b["baseSearchSerializationTokens"],
                }
                for b in detail["perToolBudgets"]
            ],
        },
    }
    write(review, review_value)
    proof = tmp_path / "proof.json"
    write(
        proof,
        {
            **detail,
            "schema": "fli.hybrid-model-pilot.exact-source-tail-proof.v1",
            "status": "accepted",
            **member,
            "frozenControlsSha256": a.sha(root / "controls.json"),
            "producerCodeSha256": {"synthetic": "producer"},
            "successfulProviderAttemptsSha256": digest(attempts),
            "inputFilesSha256": {name: a.sha(root / name) for name in paths},
            "independentReviewPath": str(review),
            "independentReviewSha256": a.sha(review),
            "unsubmittedPayloadsSha256": digest(result["toolReplies"][1:]),
            "lastFunctionCallsSha256": digest(response["output"]),
            "lastTracesSha256": digest(result["traces"][-2:]),
        },
    )
    monkeypatch.setattr(a, "SOURCE_TAIL_PROOFS", [proof])
    p = SimpleNamespace(
        private_path=lambda path: Path(path).resolve(),
        digest=digest,
        h=SimpleNamespace(hashes=lambda: {"synthetic": "producer"}),
    )
    return p, root, member, values, attempts, proof, review, source


def bound_proof(fixture):
    p, root, member, values, attempts, *_ = fixture
    result, calls, request, response = values
    return a.exact_source_tail_proof(p, root, member, [result], calls, request, response, attempts)


def test_exact_sidecar_requires_unchanged_raw_inputs_and_independent_source_artifacts(tmp_path, monkeypatch):
    fixture = bound_fixture(tmp_path, monkeypatch)
    actual = bound_proof(fixture)
    assert actual["proofSha256"] == a.sha(fixture[5])
    assert a.proof_bindings() == [{"run": fixture[2]["run"], "sha256": a.sha(fixture[5])}]


@pytest.mark.parametrize(
    "change",
    [
        "status",
        "raw",
        "missing_input",
        "tail",
        "function",
        "trace",
        "attempt",
        "source",
        "review_flag",
        "review_case",
        "review_raw_binding",
        "review_budget",
    ],
)
def test_source_sidecar_rejects_unapproved_or_changed_bindings(tmp_path, monkeypatch, change):
    fixture = bound_fixture(tmp_path, monkeypatch)
    p, root, member, values, attempts, proof, review, source = fixture
    value = a.read(proof)
    if change == "status":
        value["status"] = "pending"
    elif change == "raw":
        write(root / member["run"] / "results.json", [])
    elif change == "missing_input":
        value["inputFilesSha256"].pop("source-snapshot.json")
    elif change == "tail":
        value["unsubmittedPayloadsSha256"] = "changed"
    elif change == "function":
        value["lastFunctionCallsSha256"] = "changed"
    elif change == "trace":
        value["lastTracesSha256"] = "changed"
    elif change == "attempt":
        attempts[0]["logicalCall"] = 100
    elif change == "source":
        write(source, {"text": "invented"})
    else:
        rv = a.read(review)
        if change == "review_flag":
            rv[a.SOURCE_REVIEW_FLAGS[0]] = False
        elif change == "review_case":
            rv["case"] = "another-case"
        elif change == "review_raw_binding":
            rv["artifactBindings"].pop()
        else:
            rv["details"]["perToolBudget"][0]["remainingBeforeTool"] += 1
        write(review, rv)
        value["independentReviewSha256"] = a.sha(review)
    write(proof, value)
    with pytest.raises(ValueError):
        bound_proof(fixture)


def test_same_length_invented_source_fails_exact_payload_hash(tmp_path, monkeypatch):
    fixture = bound_fixture(tmp_path, monkeypatch)
    # No raw file is rewritten: a forged in-memory analysis projection must also fail.
    row = fixture[3][0]["toolReplies"][-1]["results"][0]
    row["text"] = row["text"].replace("Fictional", "Fabricate")
    with pytest.raises(ValueError, match="tool tail changed"):
        bound_proof(fixture)


def prior_v3_fixture(tmp_path, monkeypatch):
    root = tmp_path / "prior-run"
    root.mkdir()
    member = {"reviewId": "opaque-review", "case": "synthetic-question", "run": "synthetic-turn"}
    write(root / "controls.json", {"codeSha256": {"synthetic": "producer"}})
    write(root / "blind-map.json", [member])
    prior = HELPERS / "analysis_v3.py"
    assert a.sha(prior) == a.PRIOR_V3_APPROVED["sourceSha256"]
    approval = tmp_path / "prior-approval.json"
    approved = {
        "schema": "fli.hybrid-model-pilot.analysis-correction-acceptance.v3",
        "status": "accepted",
        "analysisVersion": a.PRIOR_V3_APPROVED["version"],
        "analysisSourceSha256": a.sha(prior),
        "acceptedPriorAnalysisBinding": a.PRIOR_APPROVED,
        "frozenControlsSha256": a.sha(root / "controls.json"),
        "producerCodeSha256": {"synthetic": "producer"},
        **dict.fromkeys(a.ACCEPTANCE_FLAGS, True),
    }
    write(approval, approved)
    # The exact historical source stays pinned. Only the approval pin is replaced
    # by this generated unit fixture, avoiding a dependency on a private receipt.
    binding = {**a.PRIOR_V3_APPROVED, "acceptanceSha256": a.sha(approval)}
    monkeypatch.setattr(a, "PRIOR_BINDINGS", [a.PRIOR_APPROVED, binding])
    batch = tmp_path / "batch"
    write(
        batch / "batch-receipt.json",
        {
            "schema": "fli.private.hybrid-model-pilot.review-batch.v1",
            "producerSha256": a.BATCH_HELPER_SHA,
            "sourceRun": str(root),
            "packets": [{"reviewId": member["reviewId"], "case": member["case"]}],
            "analysisBinding": binding,
        },
    )
    current = tmp_path / "current-approval.json"
    write(current, {"synthetic": True})
    seen = []

    def original(directory, selected):
        seen.append((directory, selected))
        return {"toolReplies": []}, [], []

    p = SimpleNamespace(
        private_path=lambda path: Path(path).resolve(),
        digest=digest,
        h=SimpleNamespace(wire_result=original, hashes=lambda: {"synthetic": "producer"}),
    )
    return p, root, member, batch, current, prior, approval, seen


def test_prior_v3_compatibility_requires_pinned_source_approval_and_unchanged_prior_verification(tmp_path, monkeypatch):
    p, root, member, batch, current, prior, approval, seen = prior_v3_fixture(tmp_path, monkeypatch)
    before = (batch / "batch-receipt.json").read_bytes()
    assert a.validate_batch_binding(p, root, batch, current, prior, approval) == "prior-accepted-v3-unchanged"
    assert seen == [(root, member)] and (batch / "batch-receipt.json").read_bytes() == before


@pytest.mark.parametrize("change", ["helper", "approval", "binding", "missing_prior", "unreviewed"])
def test_prior_v3_compatibility_rejects_untrusted_bindings(tmp_path, monkeypatch, change):
    p, root, member, batch, current, prior, approval, seen = prior_v3_fixture(tmp_path, monkeypatch)
    if change == "helper":
        changed = tmp_path / "changed-helper.py"
        changed.write_bytes(prior.read_bytes() + b"\n# Changed source\n")
        prior = changed
    elif change == "approval":
        approval.write_text("{}")
    elif change == "binding":
        value = a.read(batch / "batch-receipt.json")
        value["analysisBinding"]["sourceSha256"] = "changed"
        write(batch / "batch-receipt.json", value)
    elif change == "missing_prior":
        prior = None
    else:
        value = a.read(approval)
        value["status"] = "unreviewed"
        write(approval, value)
        binding = {**a.PRIOR_V3_APPROVED, "acceptanceSha256": a.sha(approval)}
        monkeypatch.setattr(a, "PRIOR_BINDINGS", [a.PRIOR_APPROVED, binding])
        receipt = a.read(batch / "batch-receipt.json")
        receipt["analysisBinding"] = binding
        write(batch / "batch-receipt.json", receipt)
    with pytest.raises(ValueError):
        a.validate_batch_binding(p, root, batch, current, prior, approval)
    assert seen == []


def test_new_source_tail_cannot_be_labeled_as_previously_accepted_v3(tmp_path, monkeypatch):
    p, root, member, batch, current, prior, approval, seen = prior_v3_fixture(tmp_path, monkeypatch)
    values, _ = source_fixture()
    result, calls, request, response = values
    write(root / member["run"] / "results.json", [result])
    receipts = []
    for i, call in enumerate(calls, 1):
        write(root / ("wire-" + member["run"]) / f"wire-{i:03}.json", call)
        receipts.append({"call": i, "wireSha256": digest(call["messages"]), "referenceToolMessages": 0})
        write(root / "provider" / f"request-{i:05}.json", request)
        write(root / "provider" / f"response-{i:05}.json", response)
    write(root / ("wire-" + member["run"]) / "wire-receipts.json", receipts)
    write(
        root / "provider/ledger.json",
        {"calls": [{"run": member["run"], "status": "response", "attempt": i, "logicalCall": i} for i in (1, 2)]},
    )
    write(
        root / "source-snapshot.json", {"libraries": {"fixture-library": {"documents": [{"id": "fictional-document"}]}}}
    )

    def original(*_):
        raise ValueError(a.OLD_FAILURE)

    p.h.wire_result = original
    with pytest.raises(ValueError, match="proven no-content refusal"):
        a.validate_batch_binding(p, root, batch, current, prior, approval)
