"""Versioned offline correction for local context stops; frozen producer stays untouched.

Only explicit acceptance enables the analysis wrapper. Original source-delivery
verification is attempted first, and its failure is preserved. The narrow new
branch independently proves actual provider delivery and the existing local guard.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

VERSION = "fli.hybrid-model-pilot.offline-analysis.v3"
PRE_CALL = (
    "The conversation exceeds the configured model context budget. "
    "Start a shorter conversation or configure a budget within the model's capacity. "
    "I cannot claim complete document coverage for this turn."
)
SYNTHESIS = (
    "The remaining model context budget is insufficient to synthesize an answer. "
    "Source retrieval is incomplete for this turn."
)
NO_BODY = (
    "I could identify source files but did not receive their original body text within this turn's "
    "retrieval limits. Their catalogue descriptions and outlines do not establish the requested facts, "
    "so I cannot give a source-grounded answer or claim complete coverage."
)
TOOL_LIMIT = (
    "Tool-round limit reached. Answer from content actually received. "
    "Explicitly disclose any unfinished full-document reads or catalogue pages; "
    "never claim exhaustive coverage."
)
OLD_FAILURE = "captured source replies differ from actual final provider input"
BATCH_HELPER_SHA = "89c6de97cd610a10042ba6ea820eddc2c58e335af6815cf83d153c4e2984a0bb"
PRIOR_APPROVED = {
    "version": "fli.hybrid-model-pilot.offline-analysis.v2",
    "sourceSha256": "583b41a49ca6fa5ae9704a26c240a9c9c57219b35d46b1d862dbf083f37172e8",
    "acceptanceSha256": "535b6dd14a9a55c20fe72a4573499db014d4c7757659b07dbc72a86523cc54ab",
}

ACCEPTANCE_FLAGS = (
    "narrowGuardProofIndependentlyReviewed",
    "normalAndNegativeTestsReviewed",
    "unsubmittedEvidenceNeverCredited",
    "producerAndControlsUnchanged",
    "qualityRubricUnchanged",
)


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    with path.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    path.chmod(0o600)


def overwrite_finalization(path, value):
    temporary = path.with_suffix(".analysis-tmp")
    save(temporary, value)
    temporary.replace(path)


def load_producer(repo):
    repo = Path(repo).resolve()
    require(Path.cwd().resolve() == repo, "run analysis from frozen producer checkout")
    require(Path(sys.prefix).resolve() == repo / ".venv", "use frozen producer's Python")
    sys.path.insert(0, str(repo))
    from scripts import hybrid_model_pilot as p

    return p


def provider_tool_payloads(request):
    return [json.loads(m["output"]) for m in request["input"] if m.get("type") == "function_call_output"]


def validate_unsubmitted_refusals(tail, functions, traces, source_document_ids):
    """Known no-content forms only; discovery disposition needs actual call/trace proof."""
    expected_keys = {"disposition", "documents", "error", "complete", "message", "progress", "nativeImageDelivery"}
    native = {
        "displayAvailable": [],
        "requested": [],
        "supplied": [],
        "alreadySupplied": [],
        "withheld": [],
        "reason": None,
    }
    recovery = (
        "Change strategy: scope evidence search to a selected file, expand source regions, "
        "inspect selected images, or seek a missing topic. Do not repeat the same evidence as progress."
    )
    for payload in tail:
        require(
            set(payload) == expected_keys
            and payload["disposition"] in {"budget_exceeded", "candidates_found"}
            and payload["error"] == "remaining_context_budget_exceeded"
            and payload["complete"] is False
            and payload["message"] == "No content delivered. Narrow the selection or disclose incomplete coverage."
            and payload["nativeImageDelivery"] == native,
            "unsubmitted tail is outside the proven no-content refusal class",
        )
        documents = payload["documents"]
        require(
            isinstance(documents, list)
            and all(
                isinstance(d, dict)
                and set(d) == {"documentId", "complete"}
                and d["complete"] is False
                and (d["documentId"] is None or isinstance(d["documentId"], str))
                for d in documents
            ),
            "unsubmitted refusal contains more than incomplete document markers",
        )
        if payload["disposition"] == "candidates_found":
            require(
                len(tail) == len(functions) == 1 and traces,
                "candidate refusal requires one actual discovery call and trace",
            )
            function, trace = functions[0], traces[-1]
            arguments = json.loads(function["arguments"])
            require(
                function["name"].startswith("find_documents_library_")
                and isinstance(arguments, dict)
                and arguments.get("mode") == "semantic_candidates"
                and trace.get("tool_name") == function["name"]
                and trace.get("operation") == "discover"
                and trace.get("disposition") == "candidates_found"
                and trace.get("result_count") == 0
                and trace.get("error") is None
                and "Tool content withheld because the remaining model context budget was exceeded."
                in trace.get("notes", []),
                "candidate disposition lacks actual discovery/withholding provenance",
            )
            ids = [d["documentId"] for d in documents]
            require(
                ids
                and len(set(ids)) == len(ids)
                and source_document_ids is not None
                and set(ids) <= set(source_document_ids),
                "candidate document markers differ from frozen source",
            )
        progress = payload["progress"]
        keys = {"newExtents", "repeatedExtents", "consecutiveNoProgress", "recoveryRequired"}
        require(
            isinstance(progress, dict)
            and all(type(progress.get(k)) is int and progress[k] >= 0 for k in keys - {"recoveryRequired"})
            and progress.get("recoveryRequired") is (progress["consecutiveNoProgress"] >= 2),
            "unsubmitted refusal progress is not the existing local form",
        )
        require(
            set(progress) == keys | ({"nextAction"} if progress["recoveryRequired"] else set())
            and (not progress["recoveryRequired"] or progress["nextAction"] == recovery),
            "unsubmitted refusal progress contains unsupported fields",
        )


def prove_local_stop(result, calls, request, response, *, tokenizer, grounding_state, source_document_ids=None):
    """Reject arbitrary mismatch; prove one exact frozen local-budget path.

    No result is rewritten. Returned evidence is exactly the actual final provider
    input. The extra locally observed tail remains retained and explicitly uncredited.
    """
    local = result["toolReplies"]
    final = calls[-1]
    actual = [json.loads(m["content"]) for m in final["messages"] if m.get("role") == "tool"]
    require(
        len(actual) < len(local) and actual == local[: len(actual)], "local replies are not an exact provider prefix"
    )
    require(
        provider_tool_payloads(request) == actual, "actual physical request differs from final wire source payloads"
    )
    require(response.get("status") == "completed", "last actual provider response is not complete")
    require(
        not any(m.get("role") == "user" and isinstance(m.get("content"), list) for m in final["messages"]),
        "multimodal local guard reconstruction is unsupported",
    )
    require(
        not any((r.get("nativeImageDelivery") or {}).get("supplied") for r in local),
        "native-image local guard reconstruction is unsupported",
    )
    metric = result["metrics"]["modelCalls"][-1]
    require(
        metric.get("purpose") == "tool_request"
        and metric.get("finishReason") == "tool_calls"
        and metric.get("api") == "responses"
        and metric.get("responseStatus") == "completed",
        "last saved model call is not a completed tool request",
    )
    output = response.get("output") or []
    functions = [r for r in output if r.get("type") == "function_call"]
    tail = local[len(actual) :]
    validate_unsubmitted_refusals(tail, functions, result.get("traces", []), source_document_ids)
    require(
        functions
        and len(functions) == len(tail)
        and len({f["call_id"] for f in functions}) == len(functions)
        and all(f.get("status", "completed") == "completed" for f in functions),
        "unsubmitted tool reply count/identity differs from actual returned calls",
    )
    require(all(r.get("type") in {"function_call", "reasoning", "message"} for r in output), "unsupported output item")
    texts = []
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                require(part["type"] in {"output_text", "refusal"}, "unsupported response text")
                texts.append(part["text"] if part["type"] == "output_text" else part["refusal"])
    reasoning = [r for r in output if r.get("type") == "reasoning"]
    carry = (response.get("usage", {}).get("output_tokens_details") or {}).get("reasoning_tokens") if reasoning else 0
    require(
        type(carry) is int and carry >= 0 and all(r.get("encrypted_content") for r in reasoning),
        "actual reasoning accounting unavailable",
    )
    require(carry == metric["contextCarryoverTokensEstimate"], "reasoning carryover differs")
    prompt = response["usage"]["input_tokens"]
    require(
        type(prompt) is int
        and prompt > 0
        and prompt == metric["promptTokens"]
        and response["usage"]["output_tokens"] == metric["completionTokens"],
        "provider usage differs from replay",
    )
    messages = copy.deepcopy(final["messages"])
    messages.append(
        {
            "role": "assistant",
            "content": "\n".join(texts) if texts else None,
            "tool_calls": [
                {"id": f["call_id"], "type": "function", "function": {"name": f["name"], "arguments": f["arguments"]}}
                for f in functions
            ],
        }
    )
    for function, payload in zip(functions, tail, strict=True):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": function["call_id"],
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        )
    logical = len(result["metrics"]["modelCalls"])
    if logical < 12:
        branch, expected_answer = "pre-call-context-guard", PRE_CALL
    else:
        require(logical == 12, "local synthesis guard must follow exactly twelve rounds")
        messages.append({"role": "user", "content": TOOL_LIMIT})
        needs_source, body, missing = grounding_state(result["question"], messages)
        expected_answer = (
            NO_BODY
            if needs_source and not body
            else (
                "I received original source text for only part of the identified file set. "
                f"Source retrieval for {len(missing)} file(s) remains incomplete, so I cannot give a reliable "
                "comparison across the requested files or claim complete coverage."
                if missing
                else SYNTHESIS
            )
        )
        branch = "post-tool-round-context-guard"
    require(result["answer"] == expected_answer, "answer is not the exact frozen local guard outcome")

    def size(value):
        return len(tokenizer.encode(json.dumps({"messages": value, "tools": final["tools"]})))

    sent, following = size(final["messages"]), size(messages)
    context = prompt + max(0, following - sent) + carry
    require(context + 8000 > 64000, "reconstructed local context guard would not stop")
    return actual, {
        "branch": branch,
        "answerOrigin": "existing-local-context-guard",
        "localReplyCount": len(local),
        "providerReceivedReplyCount": len(actual),
        "unsubmittedReplyCount": len(tail),
        "actualProviderInputTokens": prompt,
        "sentLocalEstimate": sent,
        "postToolLocalEstimate": following,
        "localAddedTokens": following - sent,
        "carryoverTokensEstimate": carry,
        "outputReservation": 8000,
        "reconstructedGuardTotal": context + 8000,
        "contextAllowance": 64000,
        "unsubmittedEvidenceCredited": False,
        "unsubmittedDispositions": [r["disposition"] for r in tail],
        "dispositionProvenance": (
            "Frozen local withholding preserves the original disposition; candidates_found also binds "
            "the actual semantic discovery call, corresponding trace and frozen document markers."
        ),
    }


def inspected_wire(p, original, root, member):
    """Normal path unchanged; corrected branch retains the old error and its proof."""
    try:
        result, delivered, receipts = original(root, member)
        return (result, delivered, receipts), {
            "verifier": "original-v1",
            "originalVerifierPassed": True,
            "localReplyCount": len(result["toolReplies"]),
            "providerReceivedReplyCount": len(delivered),
            "unsubmittedReplyCount": 0,
        }
    except ValueError as error:
        require(str(error) == OLD_FAILURE, "unrecognized original verifier failure: " + str(error))
    root = p.private_path(root)
    results = read(root / member["run"] / "results.json")
    require(len(results) == 1, "not an independent single-turn cell")
    result = results[0]
    receipts = read(root / ("wire-" + member["run"]) / "wire-receipts.json")
    require(len(receipts) == len(result["metrics"]["modelCalls"]), "wire/model call count differs")
    calls = []
    for ordinal, receipt in enumerate(receipts, 1):
        call = read(root / ("wire-" + member["run"]) / f"wire-{ordinal:03}.json")
        require(
            receipt["call"] == ordinal
            and p.digest(call["messages"]) == receipt["wireSha256"]
            and receipt["referenceToolMessages"] == 0,
            "wire receipts differ",
        )
        calls.append(call)
    state = read(root / "provider/ledger.json")
    attempts = [r for r in state["calls"] if r.get("run") == member["run"] and r["status"] == "response"]
    require(len(attempts) == len(receipts), "successful physical provider matrix differs")
    last = attempts[-1]
    require(
        last["logicalCall"] == max(r["logicalCall"] for r in state["calls"] if r.get("run") == member["run"]),
        "later provider attempt exists for local stop",
    )
    request_path = root / "provider" / f"request-{last['attempt']:05}.json"
    response_path = root / "provider" / f"response-{last['attempt']:05}.json"
    request, response = read(request_path), read(response_path)
    from fli.chunking import default_tokenizer
    from fli_demo.agent import grounding_state
    from scripts.score_retrieval_benchmark import delivered_bodies

    baseline = read(root / "source-snapshot.json")
    document_ids = {d["id"] for library in baseline["libraries"].values() for d in library["documents"]}
    delivered, proof = prove_local_stop(
        result,
        calls,
        request,
        response,
        tokenizer=default_tokenizer(),
        grounding_state=grounding_state,
        source_document_ids=document_ids,
    )
    # Reuse every original independent-task/history/prefix check on a temporary
    # analysis projection. Never change the raw row, its answer, or its local replies.
    projected = copy.deepcopy(results)
    projected[0]["toolReplies"] = delivered
    verified = p.h.verify_i1_wire_sources(calls, projected)
    require(verified[result["case"]] == delivered, "projected provider evidence differs")
    tail = result["toolReplies"][len(delivered) :]
    proof.update(
        {
            "verifier": VERSION,
            "originalVerifierPassed": False,
            "originalVerifierFailure": OLD_FAILURE,
            "lastActualResponseAttempt": last["attempt"],
            "lastActualLogicalCall": last["logicalCall"],
            "unsubmittedOriginalBodyFiles": len(delivered_bodies(tail)),
            "unsubmittedPayloadsSha256": p.digest(tail),
            "inputsSha256": {
                str(path.relative_to(root)): sha(path)
                for path in (
                    root / member["run"] / "results.json",
                    root / ("wire-" + member["run"]) / f"wire-{len(receipts):03}.json",
                    request_path,
                    response_path,
                )
            },
        }
    )
    return (result, delivered, receipts), proof


def collector(p, root, *, completed_only=False):
    original = p.h.wire_result
    rows = []
    for member in read(root / "blind-map.json"):
        status_path = root / member["run"] / "run-status.json"
        if completed_only and (not status_path.exists() or read(status_path).get("status") != "complete"):
            continue
        _, proof = inspected_wire(p, original, root, member)
        rows.append({"run": member["run"], "case": member["case"], "cell": member["cell"], **proof})
    return rows


def acceptance(p, run, path):
    value = read(p.private_path(path))
    require(
        value.get("schema") == "fli.hybrid-model-pilot.analysis-correction-acceptance.v3"
        and value.get("status") == "accepted"
        and value.get("analysisVersion") == VERSION
        and value.get("analysisSourceSha256") == sha(__file__)
        and value.get("acceptedPriorAnalysisBinding") == PRIOR_APPROVED
        and value.get("frozenControlsSha256") == sha(run / "controls.json")
        and value.get("producerCodeSha256") == read(run / "controls.json")["codeSha256"] == p.h.hashes()
        and all(value.get(k) is True for k in ACCEPTANCE_FLAGS),
        "explicit corrective-analysis acceptance differs",
    )
    return value


@contextmanager
def wrapped(p, root):
    original = p.h.wire_result

    def corrected(directory, member):
        require(p.private_path(directory) == root, "analysis wrapper cannot read another run")
        return inspected_wire(p, original, root, member)[0]

    p.h.wire_result = corrected
    try:
        yield
    finally:
        p.h.wire_result = original


def require_final_binding(p, root, acceptance_path):
    acceptance(p, root, acceptance_path)
    final = read(root / "finalization.json")
    require(
        final.get("schema") == "fli.hybrid-model-pilot.finalization.v3"
        and final.get("analysisVersion") == VERSION
        and final.get("analysisSourceSha256") == sha(__file__)
        and final.get("analysisAcceptanceSha256") == sha(acceptance_path)
        and sha(root / "analysis-helper.py") == sha(__file__)
        and sha(root / "analysis-acceptance.json") == sha(acceptance_path)
        and final.get("analysisDeliveryReceiptSha256") == sha(root / "analysis-delivery.json"),
        "versioned finalization analysis binding differs",
    )


def finalize(p, root, acceptance_path, deployments, runtimes, embedding):
    acceptance(p, root, acceptance_path)
    require(read(root / "run-status.json").get("status") == "pending_recheck", "producer matrix is not pending recheck")
    rows = collector(p, root)
    require(len(rows) == 192, "matrix incomplete")
    correction = {
        "schema": VERSION,
        "frozenControlsSha256": sha(root / "controls.json"),
        "producerCodeSha256": p.h.hashes(),
        "analysisSourceSha256": sha(__file__),
        "analysisAcceptanceSha256": sha(acceptance_path),
        "rows": rows,
        "originalVerifierFailures": sum(not r["originalVerifierPassed"] for r in rows),
        "qualityRubricUnchanged": True,
        "producerOrRawResultsModified": False,
        "meaning": "Provider source delivery is actual submitted prefix; local-only tails remain uncredited.",
    }
    with (root / "analysis-acceptance.json").open("xb") as stream:
        stream.write(Path(acceptance_path).read_bytes())
    (root / "analysis-acceptance.json").chmod(0o600)
    save(root / "analysis-delivery.json", correction)
    with (root / "analysis-helper.py").open("xb") as stream:
        stream.write(Path(__file__).read_bytes())
    (root / "analysis-helper.py").chmod(0o600)
    with wrapped(p, root):
        result = p.finalize(root, deployments, runtimes, embedding)
    final_path = root / "finalization.json"
    final = read(final_path)
    final.update(
        schema="fli.hybrid-model-pilot.finalization.v3",
        analysisVersion=VERSION,
        analysisSourceSha256=sha(__file__),
        analysisAcceptanceSha256=sha(acceptance_path),
        analysisDeliveryReceiptSha256=sha(root / "analysis-delivery.json"),
    )
    overwrite_finalization(final_path, final)
    return {**result, "analysisVersion": VERSION, "originalVerifierFailures": correction["originalVerifierFailures"]}


def batch_helper(path):
    require(sha(path) == BATCH_HELPER_SHA, "private batch helper changed")
    spec = importlib.util.spec_from_file_location("private_pilot_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_batch_binding(p, root, batch, acceptance_path, prior_helper_path=None, prior_acceptance_path=None):
    """Accept only unchanged v1 packets or the specifically pinned, accepted v2 analysis."""
    receipt = read(batch / "batch-receipt.json")
    expected = {"version": VERSION, "sourceSha256": sha(__file__), "acceptanceSha256": sha(acceptance_path)}
    if "analysisBinding" in receipt and receipt["analysisBinding"] == expected:
        return "accepted-v3"
    prior = None
    if "analysisBinding" in receipt:
        require(receipt["analysisBinding"] == PRIOR_APPROVED, "provisional batch used another analysis")
        require(
            prior_helper_path
            and prior_acceptance_path
            and sha(prior_helper_path) == PRIOR_APPROVED["sourceSha256"]
            and sha(prior_acceptance_path) == PRIOR_APPROVED["acceptanceSha256"],
            "prior accepted v2 helper/acceptance bytes required",
        )
        spec = importlib.util.spec_from_file_location("retained_accepted_pilot_v2", prior_helper_path)
        prior = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prior)
        prior.acceptance(p, root, prior_acceptance_path)
    require(
        receipt.get("schema") == "fli.private.hybrid-model-pilot.review-batch.v1"
        and receipt.get("producerSha256") == BATCH_HELPER_SHA
        and p.private_path(receipt["sourceRun"]) == root,
        "unbound batch is not an original private-helper batch for this run",
    )
    members = {m["reviewId"]: m for m in read(root / "blind-map.json")}
    packets = receipt.get("packets") or []
    require(
        packets and len({v["reviewId"] for v in packets}) == len(packets), "invalid original batch packet inventory"
    )
    for packet in packets:
        require(packet["reviewId"] in members, "original batch review identity is unknown")
        member = members[packet["reviewId"]]
        require(packet["case"] == member["case"], "original batch question binding differs")
        # Called before installing v3. Unbound packets require original v1;
        # explicitly pinned v2 packets must still pass the exact accepted v2 code.
        if prior is None:
            p.h.wire_result(root, member)
        else:
            prior.inspected_wire(p, p.h.wire_result, root, member)
    return "prior-accepted-v2-unchanged" if prior is not None else "original-v1-normal-path-unchanged"


def scoring(p, root, gold, reviews):
    original = p.h.wire_result
    proofs = {m["run"]: inspected_wire(p, original, root, m)[1] for m in read(root / "blind-map.json")}
    with wrapped(p, root):
        result = p.score(root, gold, reviews)
        for row in result["rows"]:
            member = next(
                m for m in read(root / "blind-map.json") if m["case"] == row["case"] and m["cell"] == row["cell"]
            )
            raw, delivered, _ = p.h.wire_result(root, member)
            row["localToolActivity"] = row["sourceActivity"]
            row["sourceActivity"] = p.trace_counts({**raw, "toolReplies": delivered})
            row["sourceActivity"]["scope"] = (
                "Actual provider-received tool replies; unsubmitted local replies excluded."
            )
            row["deliveryAnalysis"] = {
                k: v
                for k, v in proofs[member["run"]].items()
                if k
                in {
                    "verifier",
                    "originalVerifierPassed",
                    "branch",
                    "answerOrigin",
                    "localReplyCount",
                    "providerReceivedReplyCount",
                    "unsubmittedReplyCount",
                    "unsubmittedOriginalBodyFiles",
                    "unsubmittedEvidenceCredited",
                    "unsubmittedDispositions",
                    "reconstructedGuardTotal",
                    "contextAllowance",
                }
            }
        result.update(p.paired(result["rows"], p.meter_by_cell(root / "provider/ledger.json")))
    result.update(
        schema="fli.hybrid-model-pilot.scorecard.v3",
        analysisVersion=VERSION,
        analysisSourceSha256=sha(__file__),
        analysisDeliveryReceiptSha256=sha(root / "analysis-delivery.json"),
    )
    result["limitations"].append(
        "Original v1 equality verifier cannot represent local context stops; accepted versioned analysis credits only "
        "actual provider-received evidence and retains all local costs/outcomes."
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("diagnose", "finalize", "review-packets", "prepare-batch", "verify-batch", "score")
    )
    for name in (
        "producer",
        "run",
        "output",
        "acceptance",
        "deployments",
        "runtimes",
        "embedding",
        "gold",
        "reviews",
        "batch",
        "batch-helper",
        "prior-helper",
        "prior-acceptance",
    ):
        parser.add_argument("--" + name, type=Path, required=name in {"producer", "run", "output"})
    parser.add_argument("--cases", nargs="*")
    args = parser.parse_args()
    os.umask(0o077)
    p = load_producer(args.producer)
    root, output = p.private_path(args.run), p.private_path(args.output)
    require(read(root / "controls.json")["codeSha256"] == p.h.hashes(), "frozen producer changed")
    if args.operation == "diagnose":
        rows = collector(p, root, completed_only=True)
        result = {
            "schema": VERSION,
            "analysisSourceSha256": sha(__file__),
            "completedCellsChecked": len(rows),
            "correctedCells": [r for r in rows if not r["originalVerifierPassed"]],
            "acceptanceTemplate": {
                "schema": "fli.hybrid-model-pilot.analysis-correction-acceptance.v3",
                "status": "requires_independent_review",
                "analysisVersion": VERSION,
                "analysisSourceSha256": sha(__file__),
                "frozenControlsSha256": sha(root / "controls.json"),
                "producerCodeSha256": p.h.hashes(),
                "acceptedPriorAnalysisBinding": PRIOR_APPROVED,
                **dict.fromkeys(ACCEPTANCE_FLAGS, False),
            },
            "originalVerifierFailuresPreserved": True,
            "producerModified": False,
        }
        save(output, result)
    else:
        require(args.acceptance, "--acceptance required")
        acceptance(p, root, args.acceptance)
        if args.operation == "finalize":
            require(args.deployments and args.runtimes and args.embedding, "post-run captures required")
            result = finalize(
                p, root, args.acceptance, read(args.deployments), read(args.runtimes), read(args.embedding)
            )
            save(output, result)
        elif args.operation == "prepare-batch":
            require(args.gold and args.batch_helper, "--gold and --batch-helper required")
            helper = batch_helper(args.batch_helper)
            with wrapped(p, root):
                result = helper.prepare(p, root, args.gold, output, args.cases)
            receipt_path = output / "batch-receipt.json"
            receipt = read(receipt_path)
            receipt["analysisBinding"] = {
                "version": VERSION,
                "sourceSha256": sha(__file__),
                "acceptanceSha256": sha(args.acceptance),
            }
            overwrite_finalization(receipt_path, receipt)
        else:
            require_final_binding(p, root, args.acceptance)
            if args.operation == "score":
                require(args.gold and args.reviews, "--gold and --reviews required")
                result = scoring(p, root, args.gold, read(args.reviews))
                save(output, result)
            elif args.operation == "review-packets":
                require(args.gold, "--gold required")
                with wrapped(p, root):
                    result = p.review_packets(root, args.gold, output)
            else:
                require(args.batch and args.batch_helper, "--batch and --batch-helper required")
                helper = batch_helper(args.batch_helper)
                batch_path = validate_batch_binding(
                    p, root, args.batch, args.acceptance, args.prior_helper, args.prior_acceptance
                )
                with wrapped(p, root):
                    result = helper.verify_final(p, args.batch, output)
                result.update(analysisVersion=VERSION, batchAnalysisPath=batch_path)
                overwrite_finalization(output, result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k
                in {
                    "status",
                    "schema",
                    "analysisVersion",
                    "completedCellsChecked",
                    "originalVerifierFailures",
                    "packets",
                    "reviewedTurns",
                }
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
