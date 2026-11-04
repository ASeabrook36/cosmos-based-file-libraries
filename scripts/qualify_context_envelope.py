"""Offline admission replay of frozen local-context stops; makes no service calls.

The recorded final provider response and local tool candidates are held fixed.
This verifies enclosing-message admission, not changed model behavior or answers.
All inputs and output stay private; stdout contains content-free measurements.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from fli.chunking import default_tokenizer
from fli_demo.context_budget import OUTPUT_RESERVE, SYNTHESIS_PROMPT, EnvelopeContext, refusal_message


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private(path):
    path = Path(path).resolve()
    require(not any((parent / ".git").exists() for parent in (path, *path.parents)), "private path is inside Git")
    return path


def read(path):
    return json.loads(Path(path).read_text())


def checked(reference):
    path = private(reference["path"])
    require(sha(path) == reference["sha256"], "input hash changed")
    return read(path)


def bound(root, relative, expected):
    path = (root / relative).resolve()
    require(path.is_relative_to(root), "frozen path escaped run directory")
    require(sha(path) == expected, "frozen evidence hash changed")


def has_body(payload):
    if payload.get("mode") in {"outline", "catalogue", "semantic_candidates"}:
        return False
    rows = list(payload.get("results") or [])
    for document in payload.get("documents") or []:
        rows.extend(document.get("sections") or [])
    return any(row.get("text") for row in rows)


def continuation(root, diagnostic):
    for name, expected in diagnostic["inputsSha256"].items():
        bound(root, name, expected)
    run = diagnostic["run"]
    require(f"{run}/results.json" in diagnostic["inputsSha256"], "unbound result input")
    result = read(root / run / "results.json")
    require(len(result) == 1, "one frozen turn required")
    result = result[0]
    count = len(result["metrics"]["modelCalls"])
    attempt = diagnostic["lastActualResponseAttempt"]
    required = {
        f"wire-{run}/wire-{count:03}.json",
        f"provider/response-{attempt:05}.json",
        f"provider/request-{attempt:05}.json",
    }
    require(required.issubset(diagnostic["inputsSha256"]), "unbound wire/provider input")
    wire = read(root / f"wire-{run}" / f"wire-{count:03}.json")
    require(
        not any(isinstance(message.get("content"), list) for message in wire["messages"]),
        "image-bearing archived continuation requires separate qualification",
    )
    response = read(root / "provider" / f"response-{attempt:05}.json")
    request = read(root / "provider" / f"request-{attempt:05}.json")
    actual_payloads = [json.loads(message["content"]) for message in wire["messages"] if message["role"] == "tool"]
    require(
        actual_payloads
        == [json.loads(item["output"]) for item in request["input"] if item.get("type") == "function_call_output"],
        "physical request differs from wire replies",
    )
    require(result["toolReplies"][: len(actual_payloads)] == actual_payloads, "received reply prefix changed")
    tail = result["toolReplies"][len(actual_payloads) :]
    functions = [item for item in response["output"] if item["type"] == "function_call"]
    require(len(functions) == len(tail), "pending calls and local replies differ")
    texts = [part["text"] for item in response["output"] if item["type"] == "message" for part in item["content"]]
    assistant = {
        "role": "assistant",
        "content": "\n".join(texts) if texts else None,
        "tool_calls": [
            {
                "id": item["call_id"],
                "type": "function",
                "function": {"name": item["name"], "arguments": item["arguments"]},
            }
            for item in functions
        ],
    }
    terminal = [SYNTHESIS_PROMPT] if count == 12 else []
    require(count <= 12, "unexpected tool-round count")
    meter = EnvelopeContext(default_tokenizer(), wire["tools"], 64000, 8192, lambda rows: rows)
    sent = meter.local_size(wire["messages"])
    usage = response["usage"]
    carry = (
        usage["output_tokens_details"]["reasoning_tokens"]
        if any(item["type"] == "reasoning" for item in response["output"])
        else 0
    )
    meter.observe(sent, {"prompt_tokens": usage["input_tokens"]}, carry)
    candidates = [
        {
            "role": "tool",
            "tool_call_id": call["call_id"],
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        }
        for call, payload in zip(functions, tail, strict=True)
    ]
    prefix = copy.deepcopy(wire["messages"]) + [assistant]
    historical_guard = meter.size(prefix + candidates + terminal) + OUTPUT_RESERVE
    require(historical_guard == diagnostic["reconstructedGuardTotal"], "historical guard reconstruction differs")
    require(historical_guard > 64000, "historical continuation did not overflow")
    minimal = [refusal_message(call["call_id"]) for call in functions]
    preflight = meter.fits(prefix + minimal + terminal)
    messages, decisions = copy.deepcopy(prefix), []
    if preflight:
        for index, candidate in enumerate(candidates):
            reserved = minimal[index + 1 :] + terminal
            candidate_guard = meter.size(messages + [candidate] + reserved) + OUTPUT_RESERVE
            accepted = candidate_guard <= 64000
            body_tokens = len(meter.tokenizer.encode(candidate["content"]))
            before = meter.local_size(messages)
            after = meter.local_size(messages + [candidate])
            messages.append(candidate if accepted else minimal[index])
            decisions.append(
                {
                    "ordinal": index + 1,
                    "recordedCandidateContainsBody": has_body(tail[index]),
                    "candidateBodySerializationTokens": body_tokens,
                    "enclosingMessageIncrementTokens": after - before,
                    "guardWithPendingReplyAndSynthesisReservations": candidate_guard,
                    "candidateAccepted": accepted,
                    "wholePayloadRefused": not accepted,
                }
            )
        require(meter.fits(messages + terminal), "admitted continuation overflowed")
    else:
        messages.extend(minimal)
    require(messages[: len(prefix)] == prefix, "previously received context changed")
    return {
        "case": diagnostic["case"],
        "cell": diagnostic["cell"],
        "historicalGuard": historical_guard,
        "actualLastProviderInputTokens": usage["input_tokens"],
        "reasoningCarryoverEstimate": carry,
        "contextAllowance": 64000,
        "outputReservation": OUTPUT_RESERVE,
        "pendingCalls": len(candidates),
        "minimumReplyPreflightPassed": preflight,
        "wouldFetchAnyPendingSource": preflight,
        "wouldPermitNextProviderCall": preflight and meter.fits(messages + terminal),
        "resultingLocalGuard": meter.size(messages + terminal) + OUTPUT_RESERVE,
        "priorReceivedMessagesPreserved": True,
        "candidateDecisions": decisions,
        "newAnswerQualityMeasured": False,
    }


def qualify(inputs):
    require(inputs["schema"] == "fli.context-envelope-qualification.inputs.v1", "unknown input schema")
    root = private(inputs["runRoot"])
    require(sha(root / "controls.json") == inputs["frozenControlsSha256"], "frozen controls changed")
    diagnostic = checked(inputs["diagnostic"])
    require(
        len(diagnostic["correctedCells"]) == 18 and diagnostic["completedCellsChecked"] == 192, "unexpected frozen pack"
    )
    rows = [continuation(root, row) for row in diagnostic["correctedCells"]]
    [hq38] = [row for row in rows if row["case"] == "hq-38" and row["cell"] == "sol-hybrid"]
    require(hq38["historicalGuard"] == 64843, "known hq38 overflow changed")
    return {
        "schema": "fli.context-envelope-qualification.receipt.v1",
        "mode": "envelope-v2",
        "frozenBaselineCommit": "4ad228d05ed2517cda743e95470599e09898d4f9",
        "frozenControlsSha256": inputs["frozenControlsSha256"],
        "frozenDiagnosticSha256": inputs["diagnostic"]["sha256"],
        "analysisSourceSha256": sha(__file__),
        "accountingSourceSha256": sha(Path(__file__).resolve().parents[1] / "src/fli_demo/context_budget.py"),
        "agentSourceSha256": sha(Path(__file__).resolve().parents[1] / "src/fli_demo/agent.py"),
        "historicalStopsReconstructed": len(rows),
        "minimumReplyPreflightsPassed": sum(row["minimumReplyPreflightPassed"] for row in rows),
        "boundedContinuations": sum(row["wouldPermitNextProviderCall"] for row in rows),
        "preflightStopsRemain": sum(not row["minimumReplyPreflightPassed"] for row in rows),
        "candidatesAccepted": sum(d["candidateAccepted"] for row in rows for d in row["candidateDecisions"]),
        "candidatesWhollyRefused": sum(d["wholePayloadRefused"] for row in rows for d in row["candidateDecisions"]),
        "rows": rows,
        "cloudCalls": 0,
        "modelCalls": 0,
        "limitations": [
            "Counterfactual admission of fixed recorded candidates after the last actual provider response only.",
            "No changed model response, recovery strategy, final answer, or quality gain was measured.",
            "Local estimator consistency is not exact provider context-token accounting or context adequacy.",
            "No candidate or prior evidence was shortened; oversized new payloads become explicit no-body refusals.",
            "Source representation, retrieval rankings, original answers, and frozen usage remain unchanged.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = qualify(read(private(args.inputs)))
    output = private(args.output)
    with output.open("x") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    output.chmod(0o600)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
