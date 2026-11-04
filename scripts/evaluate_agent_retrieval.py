"""Opt-in real-model replay against existing libraries; no ingestion or index writes.

Captures are user-authorized evaluation artifacts, never routine telemetry. The output
must be outside this repository. Review/sanitize before publishing. Supply the same
model, budget, frozen document generations and question order for comparisons.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from fli_demo.agent import azure_chat, run_turn
from fli_demo.client import IndexClient
from fli_demo.context_budget import MODES
from fli_demo.registry import Registry
from fli_demo.responses import ResponsesProtocolError
from fli_demo.telemetry import metrics


def capture(value):
    """Native attachment credentials do not belong in even an opt-in replay capture."""
    if isinstance(value, list):
        return [capture(v) for v in value]
    if isinstance(value, dict):
        return {k: capture(v) for k, v in value.items() if k not in {"url", "expiresAt"}}
    return value


def prepare_output(output):
    """Claim a fresh private directory; a failed/partial run is never silently reused."""
    output = Path(output).expanduser().resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("evaluation output must be outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("evaluation output must be empty; preserve existing run artifacts")
    # Exclusive create also protects two runners racing for the same empty directory.
    with (output / "run-status.json").open("x") as handle:
        json.dump({"status": "running", "completedTurns": 0}, handle)
    return output


def failed_attempt_receipt(error):
    """Keep known provider counters, never an exception body or source payload.

    A protocol failure occurs after a provider response. These counters describe
    that last call, not earlier successful calls within the unfinished turn.
    Network errors and interrupts do not establish any missing usage counters.
    """
    if not isinstance(error, ResponsesProtocolError):
        return None
    usage = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = error.usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            usage[key] = value
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        values = error.usage.get(key)
        if isinstance(values, dict):
            usage[key] = {
                name: value
                for name, value in values.items()
                if name.endswith("_tokens") and isinstance(value, (int, float)) and not isinstance(value, bool)
            }
    timing = {
        key: value
        for key, value in error.timing.items()
        if key in {"totalMs", "providerMs", "rateLimitWaitMs", "rateLimitRetries"}
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }
    return {
        "scope": "final_provider_call_only; earlier calls in unfinished turn are not included",
        "usage": usage or None,
        "timing": timing or None,
        "usageAvailability": "provider_reported" if usage else "unavailable",
    }


def snapshot(index, agent):
    """Freeze catalogue metadata and model-facing context as well as source versions."""
    return capture(
        {
            "agent": agent,
            "libraries": {
                lib: {
                    "library": index.get_library(lib),
                    "documents": sorted(index.list_documents(lib), key=lambda d: d["id"]),
                    "tools": index.tools(lib, agent.get("id", "")) if hasattr(index, "tools") else None,
                }
                for lib in agent["libraryIds"]
            },
        }
    )


def indicators(case, number, answer, replies):
    """Lexical and delivery receipts only: these are not semantic correctness judgments."""
    spec = case.get("indicators") or {}
    if spec.get("perTurn"):
        spec = spec["perTurn"][number - 1]
    original_files, catalogue_files = set(), set()

    def body(row, file_name):
        if (
            file_name
            and row.get("text")
            and row.get("contentKind", "text") == "text"
            and row.get("textSource", "extracted_source_text") == "extracted_source_text"
        ):
            original_files.add(file_name)

    for reply in replies:
        if reply.get("mode") == "catalogue":
            catalogue_files.update(d["fileName"] for d in reply.get("documents", []) if d.get("fileName"))
        # Discovery descriptions and outline headings do not count as original body.
        recovered = reply.get("automaticRecovery") == "document_scoped_passages"
        if reply.get("mode") in {"catalogue", "semantic_candidates", "outline", "inspect"} and not recovered:
            continue
        refused = reply.get("disposition") == "budget_exceeded"
        if refused and not recovered:
            continue
        for result in reply.get("results") or []:
            name = result.get("fileName") or (result.get("citation") or {}).get("fileName")
            body(result, name)
            for neighbor in result.get("neighbors") or []:
                body(neighbor, name)
        for document in ([] if refused else reply.get("documents")) or []:
            for section in document.get("sections") or []:
                body(section, document.get("fileName"))
    expected = spec.get("requiredSourceFiles") or []
    minimum = spec.get("minimumSourceFiles", len(expected))
    return {
        "judgment": "lexical_and_delivery_indicators_only",
        "answerPatterns": [
            {"pattern": p, "present": bool(re.search(p, answer, re.I))} for p in spec.get("answerPatterns", [])
        ],
        "originalBodyFilesDelivered": sorted(original_files),
        "requiredSourceFiles": expected,
        "minimumSourceFiles": minimum,
        "requiredBodyDeliveryObserved": len(original_files.intersection(expected)) >= minimum if expected else None,
        "sourceFileNameMentioned": {name: name.lower() in answer.lower() for name in expected},
        "catalogueFilesDelivered": sorted(catalogue_files),
        "expectedCatalogueFilesObserved": set(spec["expectedFiles"]).issubset(catalogue_files)
        if spec.get("evidenceRequirement") == "catalogue"
        else None,
        "citationCorrectness": None,
        "taskSuccess": None,
        "notes": "Word/file-name matches and delivered body text do not establish relevance, citation correctness, "
        "complete coverage, exhaustive absence, or task success. Review against source facts.",
    }


def replay(index, chat, agent, cases, output, budget=48000, *, context_accounting="legacy-v1"):
    if context_accounting not in MODES:
        raise ValueError("unknown context accounting mode")
    output = prepare_output(output)
    if context_accounting != "legacy-v1":
        (output / "context-accounting.json").write_text(
            json.dumps({"mode": context_accounting, "contextTokens": budget, "outputReservation": 8000}, indent=2)
            + "\n"
        )
    results = []
    try:
        before = snapshot(index, agent)
        (output / "source-snapshot.json").write_text(json.dumps(before, indent=2) + "\n")
        (output / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
        for case in cases:
            # Cases are independent by default. Questions within a case deliberately share context.
            history, ledger = [], []
            for number, question in enumerate(case.get("questions") or [case["question"]], 1):
                turn = run_turn(
                    chat,
                    index,
                    agent,
                    history,
                    question,
                    context_token_budget=budget,
                    evidence_ledger=ledger,
                    **({"context_accounting": context_accounting} if context_accounting != "legacy-v1" else {}),
                )
                replies = capture([json.loads(m["content"]) for m in turn.messages if m["role"] == "tool"])
                row = {
                    "case": case["case"],
                    "turn": number,
                    "question": question,
                    "answer": turn.answer,
                    "metrics": metrics(turn),
                    "traces": capture([asdict(t) for t in turn.searches]),
                    "toolReplies": replies,
                    "indicators": indicators(case, number, turn.answer, replies),
                }
                results.append(row)
                (output / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
                history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": turn.answer}])
                print(f"{case['case']} turn {number}: captured", flush=True)
        after = snapshot(index, agent)
        unchanged = before == after
        (output / "snapshot-check.json").write_text(
            json.dumps({"unchanged": unchanged, "before": before, "after": after}, indent=2) + "\n"
        )
        if not unchanged:
            raise RuntimeError(
                "source versions, metadata or tool context changed during replay; comparison is confounded"
            )
    except (Exception, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        failed_attempt = failed_attempt_receipt(error)
        (output / "run-status.json").write_text(
            json.dumps(
                {
                    "status": "interrupted" if interrupted else "failed",
                    "completedTurns": len(results),
                    "errorType": type(error).__name__,
                    "usage": "Completed-turn usage is retained separately. Known last failed-call counters are in "
                    "failedAttempt; other unfinished-turn usage is unavailable."
                    if failed_attempt
                    else "Completed turns only; interrupted-attempt usage is unavailable."
                    if interrupted
                    else "Completed turns only; failed-attempt usage is unavailable.",
                    "failedAttempt": failed_attempt,
                },
                indent=2,
            )
            + "\n"
        )
        raise
    (output / "run-status.json").write_text(
        json.dumps({"status": "complete", "completedTurns": len(results), "budget": budget}, indent=2) + "\n"
    )
    return results


def replay_pair(
    index,
    chat_factory,
    agent,
    experiment_id,
    cases,
    output,
    budget=48000,
    repeats=3,
    index_ready=False,
    run_controls=None,
    measurement_mode="steady-state",
    context_accounting="legacy-v1",
):
    """Alternate independent single-library sessions. Preserve source-based review cases.

    index_ready is an operator observation, never inferred from successful searches.
    Source facts/regions in cases are the ground truth; no provider chunk IDs are scored.
    """
    from fli.checkpoints import digest

    if repeats < 1:
        raise ValueError("repeats must be positive")
    if context_accounting not in MODES:
        raise ValueError("unknown context accounting mode")
    if measurement_mode not in {"steady-state", "functional-smoke"}:
        raise ValueError("unknown measurement mode")
    if not index_ready and measurement_mode == "steady-state":
        raise ValueError("record index readiness before steady-state replay")
    if not run_controls or not all(run_controls.get(k) for k in ("modelRevision", "searchMode", "backgroundLoad")):
        raise ValueError("record modelRevision, searchMode and backgroundLoad controls")
    root = Path(output).expanduser().resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("evaluation output must be outside the repository")
    manifest = index.get_experiment(experiment_id)
    libraries = {k: v["libraryId"] for k, v in manifest["members"].items()}
    if set(libraries) != {"custom", "microsoft"}:
        raise ValueError("matched pair required")
    tool_context = [index.get_library(lib) for lib in libraries.values()]
    if len({(lib["name"], lib.get("description", "")) for lib in tool_context}) != 1:
        raise ValueError("pair library names and tool descriptions must be equivalent")
    snapshots = {k: index.list_documents(lib) for k, lib in libraries.items()}

    def sources(rows):
        if any(d.get("status") != "ready" or d.get("pendingGeneration") is not None for d in rows):
            raise ValueError("both libraries must be ready with no pending replacements")
        if not rows or any(not d.get("sourceId") for d in rows) or len({d.get("sourceId") for d in rows}) != len(rows):
            raise ValueError("matched source correspondence required")
        return sorted(
            (
                d["sourceId"],
                d.get("sha256"),
                d.get("fileName"),
                ("initial", d["sourceMetadataFingerprint"])
                if d.get("metadataVersion", 0) == 0 and d.get("sourceMetadataFingerprint")
                else (
                    "current",
                    d.get("author"),
                    d.get("periodStart"),
                    d.get("periodEnd"),
                    tuple(sorted(d.get("facets") or [])),
                ),
            )
            for d in rows
        )

    if sources(snapshots["custom"]) != sources(snapshots["microsoft"]):
        raise ValueError("source bytes or metadata differ between pair members")
    if manifest.get("comparison") == "controlled-component":
        # PageOverlapLength is characters, not tokens. Earlier recipes mislabeled it.
        recipes = [lib.get("ingestionProfile", {}) for lib in tool_context]
        if any(
            recipe.get("chunking", {}).get("targetTokens") != 500
            or recipe.get("chunking", {}).get("overlapTokens") != 0
            or (
                recipe.get("provider") == "microsoft-indexer"
                and recipe.get("chunking", {}).get("overlapCharacters") != 0
            )
            for recipe in recipes
        ):
            raise ValueError("controlled comparisons require 500-token targets and explicit zero overlap")
        provider_texts = [
            {d["sourceId"]: d.get("providerTextSha256") for d in snapshots[key]} for key in ("custom", "microsoft")
        ]
        tokenizers = {
            (d.get("processingMetrics") or {}).get("chunkingTokenizer") for rows in snapshots.values() for d in rows
        }
        if tokenizers != {"cl100k_base"}:
            raise ValueError("controlled comparisons require the same cl100k_base chunking tokenizer")
        if not all(provider_texts[0].values()) or provider_texts[0] != provider_texts[1]:
            raise ValueError("controlled comparisons require equal complete provider text")
    metadata = {
        "schema": "fli.paired-replay.v1",
        "experiment": manifest,
        "controls": run_controls,
        "budget": budget,
        "repeats": repeats,
        "casesHash": digest(cases),
        "indexReadiness": "operator-confirmed" if index_ready else "not-confirmed",
        "measurementMode": measurement_mode,
        "cacheState": run_controls.get("cacheState", "unavailable"),
        "quality": "unreviewed; source-fact/citation judgments required",
        "usage": "measured per turn; hosting cost and unavailable provider counters are not inferred",
        "runs": [],
    }
    if context_accounting != "legacy-v1":
        metadata["contextAccounting"] = context_accounting
    root = prepare_output(root)
    try:
        pair_agent = {**agent, "libraryIds": list(libraries.values())}
        before_context = snapshot(index, pair_agent)
        (root / "source-snapshot.json").write_text(json.dumps(before_context, indent=2) + "\n")
        (root / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
        (root / "quality-review.json").write_text(
            json.dumps(
                [
                    {
                        "case": case["case"],
                        "sourceFacts": case.get("sourceFacts"),
                        "successCriteria": case.get("successCriteria"),
                        "reviewStatus": "unreviewed",
                        "sourceEvidenceRecall": None,
                        "citationCorrectness": None,
                        "taskSuccess": None,
                        "notes": "Review against common source facts/regions, not chunk IDs.",
                    }
                    for case in cases
                ],
                indent=2,
            )
            + "\n"
        )
        (root / "comparison.json").write_text(json.dumps(metadata, indent=2) + "\n")
        for repeat in range(repeats):
            order = ["custom", "microsoft"] if repeat % 2 == 0 else ["microsoft", "custom"]
            for member in order:
                lib = libraries[member]
                # Same name/description/instructions. Profile labels never enter the model context.
                single = {**agent, "libraryIds": [lib]}
                replay(
                    index,
                    chat_factory(),
                    single,
                    cases,
                    root / f"run-{repeat + 1}-{member}",
                    budget,
                    **({"context_accounting": context_accounting} if context_accounting != "legacy-v1" else {}),
                )
                metadata["runs"].append({"repeat": repeat + 1, "member": member, "order": len(metadata["runs"]) + 1})
                (root / "comparison.json").write_text(json.dumps(metadata, indent=2) + "\n")
        after_context = snapshot(index, pair_agent)
        metadata["sourceUnchanged"] = before_context == after_context
        (root / "snapshot-check.json").write_text(
            json.dumps(
                {"unchanged": metadata["sourceUnchanged"], "before": before_context, "after": after_context}, indent=2
            )
            + "\n"
        )
        (root / "comparison.json").write_text(json.dumps(metadata, indent=2) + "\n")
        if not metadata["sourceUnchanged"]:
            raise RuntimeError("pair inventory or tool context changed across runs; comparison is confounded")
    except (Exception, KeyboardInterrupt) as error:
        (root / "run-status.json").write_text(
            json.dumps(
                {
                    "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                    "completedRuns": len(metadata["runs"]),
                    "errorType": type(error).__name__,
                },
                indent=2,
            )
            + "\n"
        )
        raise
    (root / "run-status.json").write_text(
        json.dumps({"status": "complete", "completedRuns": len(metadata["runs"])}, indent=2) + "\n"
    )
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--cases", type=Path, default=Path("docs/testing/2026-10-27/casual-cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-tokens", type=int, default=48000)
    parser.add_argument("--context-accounting", choices=MODES, default="legacy-v1")
    parser.add_argument("--experiment-id")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--index-ready", action="store_true")
    parser.add_argument("--measurement-mode", choices=["steady-state", "functional-smoke"], default="steady-state")
    parser.add_argument(
        "--run-controls", type=Path, help="private JSON: modelRevision, searchMode, backgroundLoad, cacheState"
    )
    args = parser.parse_args()
    if args.experiment_id:
        replay_pair(
            IndexClient.from_env(),
            lambda: azure_chat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"]),
            Registry().get(args.agent_id),
            args.experiment_id,
            json.loads(args.cases.read_text()),
            args.output,
            args.context_tokens,
            args.repeats,
            args.index_ready,
            json.loads(args.run_controls.read_text()) if args.run_controls else None,
            args.measurement_mode,
            args.context_accounting,
        )
        return
    replay(
        IndexClient.from_env(),
        azure_chat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"]),
        Registry().get(args.agent_id),
        json.loads(args.cases.read_text()),
        args.output,
        args.context_tokens,
        context_accounting=args.context_accounting,
    )


if __name__ == "__main__":
    main()
