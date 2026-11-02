"""P1 saved-evidence equivalence and serial, dev-only real-model replay.

No provisioning, ingestion, or source mutation. Environment bindings/captures stay
private. Baseline, format-only and selector-only cells use one frozen new library.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

from fli.chunking import default_tokenizer
from fli.presentation import evidence_json
from fli.reference_format import CONTRACT, pack, unpack
from fli.selector_hints import add_hints, tool_hints
from fli.structure_lab import digest
from fli_demo.client import IndexClient
from scripts.compare_agent_models import code_hashes, default_chat_factory
from scripts.evaluate_agent_retrieval import capture, prepare_output, replay, snapshot
from scripts.retrieval_benchmark import validate_sources
from scripts.retrieval_controls import (
    AGENT_INSTRUCTIONS,
    ANSWER_CONTROLS,
    answer_controls,
    deployment_binding,
    frozen_pack,
    private_path,
    read,
    verify_cell,
)
from scripts.score_retrieval_benchmark import (
    FAILURES,
    QUALITY,
    coverage,
    relevant_source_coverage,
    usage,
    validate_gold,
)
from scripts.structure_lab import save

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "docs/testing/retrieval-p1-controls.json"
CELLS = ("baseline", "format-only", "selectors-only")


def hashes():
    return {
        **code_hashes(),
        **{
            str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), PLAN, REPO / "scripts/retrieval_controls.py")
        },
    }


def measure(saved_run, output):
    """Sanitized counts from all saved R0 development replies; no answering model."""
    saved_run = private_path(saved_run)
    controls = read(saved_run / "controls.json")
    answer_controls(controls)
    root = prepare_output(private_path(output))
    tokenizer, rows = default_tokenizer(), []
    for cell in controls["schedule"]:
        directory = saved_run / f"r{cell['repeat']}-{cell['role']}"
        verify_cell(directory, controls["cases"])
        for turn in read(directory / "results.json"):
            for ordinal, evidence in enumerate(turn["toolReplies"], 1):
                encoded = pack(evidence)
                if unpack(encoded) != evidence:
                    raise AssertionError("source evidence changed during factoring")
                original_tokens = len(tokenizer.encode(evidence_json(evidence)))
                reference_tokens = len(tokenizer.encode(evidence_json(encoded)))
                hints = add_hints(evidence)
                rows.append(
                    {
                        **cell,
                        "case": turn["case"],
                        "turn": turn["turn"],
                        "reply": ordinal,
                        "sourceSha256": digest(evidence),
                        "referenceSha256": digest(encoded),
                        "exactReconstruction": True,
                        "currentTokensEstimate": original_tokens,
                        "referenceTokensEstimate": reference_tokens,
                        "selectorTokensEstimate": len(tokenizer.encode(evidence_json(hints))),
                        "mode": evidence.get("mode", "error"),
                    }
                )
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["role"]].append(row)
    summary = {}
    for role, values in grouped.items():
        before = sum(v["currentTokensEstimate"] for v in values)
        after = sum(v["referenceTokensEstimate"] for v in values)
        summary[role] = {
            "replies": len(values),
            "currentTokensEstimate": before,
            "referenceTokensEstimate": after,
            "reductionFraction": 1 - after / before,
            "targetMet": after <= 0.8 * before,
        }
    result = {
        "schema": "fli.p1.saved-response.v1",
        "sourceRunControlsSha256": digest(controls),
        "planSha256": digest(read(PLAN)),
        "tokenizer": tokenizer.name,
        "serialization": "UTF-8 JSON ensure_ascii=False separators=(',', ':')",
        "exactReconstruction": True,
        "heldoutExecuted": False,
        "actualProviderTokensMeasured": False,
        "actualLatencyMeasured": False,
        "interpretation": "Presentation equivalence and estimated serialization only. No extraction fidelity repaired.",
        "codeSha256": hashes(),
        "summary": summary,
        "rows": rows,
    }
    save(root / "receipt.json", result)
    save(root / "run-status.json", {"status": "complete", "replies": len(rows)})
    return result


class RecordedChat:
    """Preserve private actual wire evidence and content-free token receipts."""

    def __init__(self, chat, directory, cell):
        self.chat, self.directory, self.cell = chat, directory, cell
        self.calls = []
        self.tokenizer = default_tokenizer()

    def __call__(self, messages, tools):
        wire = copy.deepcopy(messages)
        canonical = copy.deepcopy(messages)
        references = 0
        for message in canonical:
            if message.get("role") == "tool":
                payload = json.loads(message["content"])
                if payload.get("contract") == CONTRACT:
                    references += 1
                    message["content"] = evidence_json(unpack(payload))
        if (self.cell == "format-only") != bool(references) and any(m.get("role") == "tool" for m in messages):
            raise ValueError("wire evidence format differs from comparison cell")
        ordinal = len(self.calls) + 1
        # capture removes native attachment credentials but retains source text privately.
        save(self.directory / f"wire-{ordinal:03}.json", {"messages": capture(wire), "tools": tools})
        row = {
            "call": ordinal,
            "wireSha256": digest(capture(wire)),
            "wireInputTokensEstimate": len(self.tokenizer.encode(evidence_json({"messages": wire, "tools": tools}))),
            "expandedInputTokensEstimate": len(
                self.tokenizer.encode(evidence_json({"messages": canonical, "tools": tools}))
            ),
            "referenceToolMessages": references,
        }
        self.calls.append(row)
        save(self.directory / "wire-receipts.json", self.calls)
        return self.chat(messages, tools)


def make_index(config, cell):
    return IndexClient(
        config["indexUrl"],
        os.environ["DEV_API_KEY"],
        evidence_view="references" if cell == "format-only" else "compact",
        selector_mode="handles" if cell == "selectors-only" else "legacy",
    )


def bound_deployment(envelope, config):
    return deployment_binding(
        envelope,
        configured_endpoint=os.environ["AOAI_ENDPOINT"],
        configured_deployment=config["model"]["deployment"],
        model=config["model"]["model"],
        revision=config["model"]["version"],
    )


def run(config, before_receipt, output):
    manifest, tasks = frozen_pack(REPO / "docs/testing/retrieval-r0")
    plan = read(PLAN)
    if config["agent"]["instructions"] != AGENT_INSTRUCTIONS or config.get("searchMode") != "vector":
        raise ValueError("agent/query controls differ from R0")
    if set(config) != {"model", "agent", "libraryId", "indexUrl", "searchMode"}:
        raise ValueError("unexpected private configuration fields")
    cases = [case for case in tasks if case["case"] in plan["cases"]]
    if len(cases) != len(plan["cases"]) or any(c["split"] != "development" for c in cases):
        raise ValueError("P1 requires frozen development tasks")
    controls = {
        **ANSWER_CONTROLS,
        "model": config["model"],
        "agent": config["agent"],
        "plan": plan,
        "cases": cases,
        "heldoutExecuted": False,
        "sourceStrategy": "microsoft-indexer-layout-v1",
        "searchMode": "vector",
    }
    answer_controls(controls)
    binding = bound_deployment(before_receipt, config)
    root = prepare_output(private_path(output))
    initial_hashes = hashes()
    controls.update(codeSha256=initial_hashes, deploymentBinding=binding)
    agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
    index = make_index(config, "baseline")
    baseline = snapshot(index, agent)
    validate_sources(baseline, {"libraries": {"microsoft": config["libraryId"]}}, manifest)
    save(root / "source-snapshot.json", baseline)
    save(root / "controls.json", controls)
    save(root / "private-config.json", config)
    save(root / "deployment-before.json", before_receipt)
    completed = 0
    try:
        for repeat in range(1, plan["repeats"] + 1):
            for cell in CELLS if repeat % 2 else tuple(reversed(CELLS)):
                if hashes() != initial_hashes or snapshot(index, agent) != baseline:
                    raise RuntimeError("source or code drift before P1 cell")
                active = root / f"r{repeat}-{cell}"
                member = make_index(config, cell)
                expected = copy.deepcopy(baseline)
                if cell == "selectors-only":
                    for library in expected["libraries"].values():
                        library["tools"] = tool_hints(library["tools"])
                if snapshot(member, agent) != expected:
                    raise RuntimeError("unexpected cell source/tool difference")
                wire_directory = root / f"wire-r{repeat}-{cell}"
                wire_directory.mkdir(mode=0o700)
                chat = RecordedChat(default_chat_factory(config["model"], "responses"), wire_directory, cell)
                result = replay(member, chat, agent, copy.deepcopy(cases), active, budget=200000)
                verify_cell(active, cases, expected)
                completed += len(result)
                if hashes() != initial_hashes or snapshot(index, agent) != baseline:
                    raise RuntimeError("source or code drift after P1 cell")
                save(
                    root / "run-status.json",
                    {
                        "status": "running",
                        "completedTurns": completed,
                        "snapshotsUnchanged": True,
                        "heldoutExecuted": False,
                    },
                )
    except (Exception, KeyboardInterrupt) as error:
        save(
            root / "run-status.json",
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                "completedTurns": completed,
                "errorType": type(error).__name__,
                "unfinishedUsage": "Cell receipt retains completed turns; unavailable counters are not zero.",
            },
        )
        raise
    result = {
        "status": "pending_deployment_recheck",
        "completedTurns": completed,
        "snapshotsUnchanged": True,
        "heldoutExecuted": False,
    }
    save(root / "run-status.json", result)
    return result


def finalize(directory, after_receipt):
    root = private_path(directory)
    config, controls = read(root / "private-config.json"), read(root / "controls.json")
    if bound_deployment(after_receipt, config) != controls["deploymentBinding"]:
        raise ValueError("answering deployment changed during comparison")
    if hashes() != controls["codeSha256"]:
        raise ValueError("code changed during measured window")
    if read(root / "run-status.json")["status"] != "pending_deployment_recheck":
        raise ValueError("only a fully executed comparison can be finalized")
    index = make_index(config, "baseline")
    agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
    baseline = read(root / "source-snapshot.json")
    if snapshot(index, agent) != baseline:
        raise ValueError("source changed after final comparison")
    for repeat in range(1, 3):
        for cell in CELLS:
            expected = copy.deepcopy(baseline)
            if cell == "selectors-only":
                for library in expected["libraries"].values():
                    library["tools"] = tool_hints(library["tools"])
            verify_cell(root / f"r{repeat}-{cell}", controls["cases"], expected)
    save(root / "deployment-after.json", after_receipt)
    status = {
        "status": "complete",
        "completedTurns": 18,
        "snapshotsUnchanged": True,
        "deploymentUnchanged": True,
        "heldoutExecuted": False,
    }
    save(root / "run-status.json", status)
    return status


def score(directory, gold, reviews):
    """Export only allowlisted counters and separately reviewed source quality."""
    root = private_path(directory)
    manifest, tasks = frozen_pack(REPO / "docs/testing/retrieval-r0")
    validate_gold(manifest, tasks, gold)
    controls = read(root / "controls.json")
    answer_controls(controls)
    status = read(root / "run-status.json")
    if status != {
        "status": "complete",
        "completedTurns": 18,
        "snapshotsUnchanged": True,
        "deploymentUnchanged": True,
        "heldoutExecuted": False,
    }:
        raise ValueError("P1 comparison is not completely finalized")
    cases = [c for c in tasks if c["case"] in read(PLAN)["cases"]]
    if controls["cases"] != cases or controls["plan"] != read(PLAN):
        raise ValueError("scoring inputs differ from predeclared plan")
    by_case = {c["case"]: c for c in cases}
    expected_keys = {(repeat, cell, c["case"], 1) for repeat in (1, 2) for cell in CELLS for c in cases}
    judgments = {}
    for review in reviews:
        key = review["repeat"], review["cell"], review["case"], review.get("turn", 1)
        if key not in expected_keys or key in judgments or type(key[0]) is not int or type(key[3]) is not int:
            raise ValueError("unexpected or duplicate P1 judgment")
        if any(
            field not in review or (review[field] is not None and type(review[field]) is not bool) for field in QUALITY
        ):
            raise ValueError("all four source-quality fields must be explicitly reviewed or marked inapplicable")
        if not set(review.get("failureCategories", [])) <= FAILURES:
            raise ValueError("unknown failure category")
        judgments[key] = review
    if set(judgments) != expected_keys:
        raise ValueError("every executed P1 turn needs source-based review")
    baseline, rows, wire_rows = read(root / "source-snapshot.json"), [], []
    for repeat in (1, 2):
        for cell in CELLS:
            expected = copy.deepcopy(baseline)
            if cell == "selectors-only":
                for library in expected["libraries"].values():
                    library["tools"] = tool_hints(library["tools"])
            directory = root / f"r{repeat}-{cell}"
            verify_cell(directory, cases, expected)
            results = read(directory / "results.json")
            wires = read(root / f"wire-r{repeat}-{cell}/wire-receipts.json")
            if len(wires) != sum(len(r["metrics"]["modelCalls"]) for r in results):
                raise ValueError("wire/model call receipts are incomplete")
            for ordinal, wire in enumerate(wires, 1):
                captured = read(root / f"wire-r{repeat}-{cell}/wire-{ordinal:03}.json")
                if wire["call"] != ordinal or digest(captured["messages"]) != wire["wireSha256"]:
                    raise ValueError("wire evidence changed or is missing")
                wire_rows.append(
                    {
                        "repeat": repeat,
                        "cell": cell,
                        **{
                            key: wire[key]
                            for key in (
                                "call",
                                "wireSha256",
                                "wireInputTokensEstimate",
                                "expandedInputTokensEstimate",
                                "referenceToolMessages",
                            )
                        },
                    }
                )
            for result in results:
                key = repeat, cell, result["case"], result["turn"]
                review, case = judgments[key], by_case[result["case"]]
                categories = defaultdict(int)
                for reply in result["toolReplies"]:
                    error = reply.get("errorCategory") or reply.get("error")
                    if error:
                        matched = next(
                            (
                                name
                                for name in (
                                    "source_scope_unavailable",
                                    "evidence_changed",
                                    "invalid_request",
                                    "invalid_selector",
                                    "document_not_found",
                                    "document_changed",
                                )
                                if name in str(error)
                            ),
                            "retrieval_error",
                        )
                        categories[matched] += 1
                rows.append(
                    {
                        "repeat": repeat,
                        "cell": cell,
                        "case": result["case"],
                        "turn": result["turn"],
                        "sourceDelivery": coverage(case, result["toolReplies"], gold),
                        "relevantSourceCoverage": relevant_source_coverage(case, review["relevantSourceCoverage"]),
                        "quality": {field: review[field] for field in QUALITY},
                        "failureCategories": review.get("failureCategories", []),
                        "errorCategories": dict(categories),
                        "usage": usage(result),
                    }
                )
    summary = {}
    for cell in CELLS:
        members = [r for r in rows if r["cell"] == cell]
        counters = {}
        for field in (
            "promptTokens",
            "completionTokens",
            "totalTokens",
            "rateLimitRetries",
            "rateLimitWaitMs",
            "retrievalRequestCharge",
            "totalMs",
            "toolErrors",
            "toolBudgetRefusals",
        ):
            values = [r["usage"][field] for r in members]
            counters[field] = sum(values) if all(v is not None for v in values) else None
            counters[field + "KnownSubtotal"] = sum(v for v in values if v is not None)
        counters["retrievalChargeComplete"] = all(r["usage"]["retrievalChargeComplete"] is True for r in members)
        if not counters["retrievalChargeComplete"]:
            counters["retrievalRequestCharge"] = None
        summary[cell] = {
            "turns": len(members),
            "quality": {
                field: {
                    "passed": sum(r["quality"][field] for r in members if r["quality"][field] is not None),
                    "applicable": sum(r["quality"][field] is not None for r in members),
                }
                for field in QUALITY
            },
            "usage": counters,
        }
    return {
        "schema": "fli.p1.scorecard.v1",
        "status": "complete",
        "heldoutExecuted": False,
        "contextAllowancePerRun": 200000,
        "model": {"model": "gpt-6.1-sol", "version": "2026-09-29"},
        "controlsSha256": digest(controls),
        "codeSha256": controls["codeSha256"],
        "reviewMethod": "source-based assistant review; not blinded or independent human accuracy",
        "meaning": (
            "Small functional paired pilot; token estimates and provider usage are separate; "
            "no latency or production winner."
        ),
        "summary": summary,
        "rows": rows,
        "wireReceipts": wire_rows,
        "unavailable": [
            "billed currency",
            "connector/provider ingestion usage",
            "allocated hosting",
            "actual inference region",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["measure", "replay", "finalize", "score"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "measure":
        if not args.source:
            parser.error("measure requires --source")
        result = measure(args.source, args.output)
        print(json.dumps(result["summary"]))
    elif args.action == "score":
        if not args.source or not args.gold or not args.reviews:
            parser.error("score requires --source, --gold and --reviews")
        result = score(args.source, read(private_path(args.gold)), read(private_path(args.reviews)))
        save(args.output, result)
        print(json.dumps(result["summary"]))
    else:
        if not args.deployment or (args.action == "replay" and not args.config):
            parser.error("replay/finalize require private --deployment and replay also --config")
        envelope = read(private_path(args.deployment))
        result = (
            run(read(private_path(args.config)), envelope, args.output)
            if args.action == "replay"
            else finalize(args.output, envelope)
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
