"""Controlled model/context experiment using the existing agent replay harness.

The config and all captures must stay outside the repository. This only reads
existing libraries; it does not deploy models, ingest files or change the demo.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

from fli_demo.agent import azure_chat
from fli_demo.client import IndexClient
from scripts.evaluate_agent_retrieval import failed_attempt_receipt, prepare_output, replay, snapshot

REPO = Path(__file__).resolve().parents[1]
ORIGINAL = ("current", "complete-custom", "complete-microsoft", "controlled-custom", "controlled-microsoft")
HELDOUT = ("heldout-current", "heldout-custom", "heldout-microsoft")
CONTEXT = ("complete-microsoft", "controlled-microsoft")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def code_hashes():
    paths = sorted((REPO / "src").rglob("*.py")) + [
        Path(__file__).resolve(),
        REPO / "scripts/evaluate_agent_retrieval.py",
    ]
    return {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def schedule(repeats=2):
    """Finish all 24k comparisons before varying context for mini alone."""
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    rows = []
    for repeat in range(1, repeats + 1):
        for library in CONTEXT:
            rows.append(
                {
                    "id": f"24k-bridge-r{repeat}-{library}-mini",
                    "stage": "transport-bridge",
                    "transport": "chat-completions",
                    "pack": "original",
                    "repeat": repeat,
                    "library": library,
                    "model": "mini",
                    "budget": 24000,
                }
            )
    for pack, libraries in [("original", ORIGINAL), ("heldout", HELDOUT)]:
        for repeat in range(1, repeats + 1):
            for library in libraries:
                for model in ("mini", "strong") if repeat % 2 else ("strong", "mini"):
                    rows.append(
                        {
                            "id": f"24k-{pack}-r{repeat}-{library}-{model}",
                            "stage": "model",
                            "transport": "responses",
                            "pack": pack,
                            "repeat": repeat,
                            "library": library,
                            "model": model,
                            "budget": 24000,
                        }
                    )
    for repeat in range(1, repeats + 1):
        for library in CONTEXT:
            rows.append(
                {
                    "id": f"64k-original-r{repeat}-{library}-mini",
                    "stage": "context",
                    "transport": "responses",
                    "pack": "original",
                    "repeat": repeat,
                    "library": library,
                    "model": "mini",
                    "budget": 64000,
                }
            )
    return rows


def validate_config(config):
    if set(config) - {"models", "libraries", "agent", "controls"}:
        raise ValueError("config accepts only models, libraries, agent and controls; use environment for credentials")
    if set(config.get("models", {})) != {"mini", "strong"}:
        raise ValueError("models must contain mini and strong")
    for model in config["models"].values():
        if set(model) != {"deployment", "model", "version"} or not all(
            isinstance(value, str) and value.strip() for value in model.values()
        ):
            raise ValueError("each model needs deployment, model and pinned version")
    if set(config.get("libraries", {})) != set(ORIGINAL + HELDOUT):
        raise ValueError("the eight existing library roles are required")
    libraries = list(config["libraries"].values())
    if not all(isinstance(value, str) and value.strip() for value in libraries) or len(set(libraries)) != 8:
        raise ValueError("library roles must identify eight distinct libraries")
    if not isinstance(config.get("agent"), dict) or not config["agent"].get("id"):
        raise ValueError("an unchanged agent identity and instructions are required")
    if not config["agent"].get("instructions"):
        raise ValueError("agent instructions are required")


def validate_sources(frozen, libraries):
    for roles in (ORIGINAL, HELDOUT):
        identities = []
        for role in roles:
            documents = frozen["libraries"][libraries[role]]["documents"]
            if not documents or any(
                d.get("status") != "ready" or d.get("pendingGeneration") or not d.get("sha256") for d in documents
            ):
                raise ValueError("all comparison sources must be ready, hashed and without a pending generation")
            identities.append(sorted((d.get("fileName"), d["sha256"]) for d in documents))
        if any(identity != identities[0] for identity in identities[1:]):
            raise ValueError("source filenames/bytes differ across ingestion configurations")


def default_chat_factory(model, transport):
    if transport == "chat-completions":
        return azure_chat(
            os.environ["AOAI_ENDPOINT"],
            model["deployment"],
            reasoning_effort="medium",
            max_completion_tokens=8000,
            max_attempts=3,
        )
    from fli_demo.responses import azure_responses_chat

    return azure_responses_chat(
        os.environ["AOAI_ENDPOINT"],
        model["deployment"],
        reasoning_effort="medium",
        max_output_tokens=8000,
        max_attempts=3,
    )


def compare(
    index,
    config,
    original_cases,
    heldout_cases,
    output,
    *,
    repeats=2,
    chat_factory=default_chat_factory,
    replay_fn=replay,
    code_hash_fn=code_hashes,
):
    config = copy.deepcopy(config)
    validate_config(config)
    packs = {"original": copy.deepcopy(original_cases), "heldout": copy.deepcopy(heldout_cases)}
    if [c["case"] for c in packs["original"]] != ["deadline", "compare-options"]:
        raise ValueError("original cases must be deadline and compare-options, in that order")
    if [c["case"] for c in packs["heldout"]] != ["heldout-form-comparison"]:
        raise ValueError("heldout pack must contain heldout-form-comparison")
    runs = schedule(repeats)
    root = prepare_output(output)
    global_agent = {**config["agent"], "libraryIds": list(config["libraries"].values())}
    completed = []
    active = None
    before = None
    initial_code = code_hash_fn()
    controls = {
        "experiment": "model-then-context-v1",
        "config": config,
        "transport": "Azure OpenAI Responses v1; store=false; no server-side conversation",
        "transportBridge": "Fresh mini Chat Completions runs precede model stage; compare to matching Responses cells. "
        "This measures API mapping plus preserved reasoning continuity, not wire transport alone.",
        "reasoningEffort": "medium",
        "reasoningCompute": "Same effort label does not imply equal internal computation; reported tokens retained",
        "maxOutputTokens": 8000,
        "maxAttempts": 3,
        "measurementMode": "functional-smoke",
        "modelOrder": "mini/strong on odd repeats; strong/mini on even repeats; serial execution",
        "contextComparison": "mini only, 24k versus 64k, both Microsoft configurations; no model change",
        "cacheState": "provider cache uncontrolled; reported usage retained",
        "capacityLimits": "Configured token/minute caps do not establish identical service capacity or scheduling; "
        "retain observed quota waits and do not infer a latency/cost winner from them.",
        "inferenceRegion": "GlobalStandard actual inference region unavailable",
        "quality": "source-based review required; lexical indicators are not correctness scores",
        "cases": packs,
        "codeSha256": initial_code,
        "runs": runs,
        "plannedTurns": sum(len(packs[row["pack"]]) for row in runs),
    }
    write_json(root / "controls.json", controls)
    write_json(
        root / "quality-review.json",
        [
            {
                "run": row["id"],
                "case": case["case"],
                "reviewStatus": "unreviewed",
                "taskSuccess": None,
                "sourceSupport": None,
                "citationReview": None,
                "decisiveEvidenceDelivered": None,
                "notes": [],
            }
            for row in runs
            for case in packs[row["pack"]]
        ],
    )
    try:
        before = snapshot(index, global_agent)
        write_json(root / "source-snapshot.json", before)
        validate_sources(before, config["libraries"])
        for active in runs:
            # Catch changes between runs, as well as replay's within-run checks.
            if snapshot(index, global_agent) != before or code_hash_fn() != initial_code:
                raise RuntimeError("source/library/tool context or code changed; comparison is confounded")
            print(f"Starting {active['id']}", flush=True)
            agent = {**config["agent"], "libraryIds": [config["libraries"][active["library"]]]}
            results = replay_fn(
                index,
                chat_factory(copy.deepcopy(config["models"][active["model"]]), active["transport"]),
                agent,
                copy.deepcopy(packs[active["pack"]]),
                root / active["id"],
                budget=active["budget"],
            )
            completed.append({"run": active["id"], "turns": len(results)})
            write_json(root / "completed-runs.json", completed)
            write_json(
                root / "run-status.json",
                {"status": "running", "completedTurns": sum(row["turns"] for row in completed)},
            )
        after = snapshot(index, global_agent)
        unchanged = before == after and code_hash_fn() == initial_code
        write_json(root / "snapshot-check.json", {"unchanged": unchanged, "before": before, "after": after})
        if not unchanged:
            raise RuntimeError("source/library/tool context or code changed; comparison is confounded")
    except (Exception, KeyboardInterrupt) as error:
        partial = 0
        if active and active["id"] not in {row["run"] for row in completed}:
            receipt = root / active["id"] / "run-status.json"
            if receipt.exists():
                partial = json.loads(receipt.read_text()).get("completedTurns", 0)
        write_json(
            root / "run-status.json",
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                "completedTurns": sum(row["turns"] for row in completed) + partial,
                "activeRun": active["id"] if active else None,
                "errorType": type(error).__name__,
                "usage": "Retained completed turns only; failed/interrupted attempt usage may be unavailable.",
                "failedAttempt": failed_attempt_receipt(error),
            },
        )
        raise
    result = {"status": "complete", "completedTurns": sum(row["turns"] for row in completed), "unchanged": True}
    write_json(root / "run-status.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Private model/library/agent configuration")
    parser.add_argument("--output", type=Path, required=True, help="Fresh private capture directory")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.config.expanduser().resolve().is_relative_to(REPO):
        parser.error("private configuration must be outside the repository")
    config = json.loads(args.config.read_text())
    original = json.loads((REPO / "docs/testing/2026-11-01/retrieval-cases.json").read_text())[:2]
    heldout = [
        case
        for case in json.loads((REPO / "docs/testing/2026-11-01/heldout-cases.json").read_text())
        if case["case"] == "heldout-form-comparison"
    ]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    config.setdefault("controls", {})["gitRevision"] = revision
    compare(IndexClient.from_env(), config, original, heldout, args.output, repeats=args.repeats)


if __name__ == "__main__":
    main()
