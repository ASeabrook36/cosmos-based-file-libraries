"""Opt-in real-model replay against existing libraries; no ingestion or index writes.

Captures are user-authorized evaluation artifacts, never routine telemetry. The output
must be outside this repository. Review/sanitize before publishing. Supply the same
model, budget, frozen document generations and question order for comparisons.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from fli_demo.agent import azure_chat, run_turn
from fli_demo.client import IndexClient
from fli_demo.registry import Registry
from fli_demo.telemetry import metrics


def capture(value):
    """Native attachment credentials do not belong in even an opt-in replay capture."""
    if isinstance(value, list):
        return [capture(v) for v in value]
    if isinstance(value, dict):
        return {k: capture(v) for k, v in value.items() if k not in {"url", "expiresAt"}}
    return value


def replay(index, chat, agent, cases, output, budget=48000):
    output = Path(output).expanduser().resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("evaluation output must be outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    results = []
    before = {lib: index.list_documents(lib) for lib in agent["libraryIds"]}
    for case in cases:
        # Cases are independent by default. Questions within a case deliberately share context.
        history, ledger = [], []
        for number, question in enumerate(case.get("questions") or [case["question"]], 1):
            turn = run_turn(chat, index, agent, history, question, context_token_budget=budget, evidence_ledger=ledger)
            row = {
                "case": case["case"],
                "turn": number,
                "question": question,
                "answer": turn.answer,
                "metrics": metrics(turn),
                "traces": capture([asdict(t) for t in turn.searches]),
                "toolReplies": [json.loads(m["content"]) for m in turn.messages if m["role"] == "tool"],
            }
            results.append(row)
            (output / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
            history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": turn.answer}])
            print(f"{case['case']} turn {number}: captured", flush=True)
    after = {lib: index.list_documents(lib) for lib in agent["libraryIds"]}

    def versions(rows):
        return sorted(
            (
                lib,
                d["id"],
                d.get("activeGeneration"),
                d.get("contentRevision"),
                (d.get("fullText") or {}).get("sha256"),
                (d.get("imageAssets") or {}).get("sha256"),
                d.get("sha256"),
                (d.get("ingestionJob") or {}).get("executionId"),
            )
            for lib, docs in rows.items()
            for d in docs
        )

    unchanged = versions(before) == versions(after)
    (output / "snapshot-check.json").write_text(
        json.dumps({"unchanged": unchanged, "before": versions(before), "after": versions(after)}, indent=2) + "\n"
    )
    if not unchanged:
        raise RuntimeError("source versions changed during replay; comparison is confounded")
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
):
    """Alternate independent single-library sessions. Preserve source-based review cases.

    index_ready is an operator observation, never inferred from successful searches.
    Source facts/regions in cases are the ground truth; no provider chunk IDs are scored.
    """
    from fli.checkpoints import digest

    if repeats < 1:
        raise ValueError("repeats must be positive")
    if not index_ready:
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
        "indexReadiness": "operator-confirmed",
        "cacheState": run_controls.get("cacheState", "unavailable"),
        "quality": "unreviewed; source-fact/citation judgments required",
        "usage": "measured per turn; hosting cost and unavailable provider counters are not inferred",
        "runs": [],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "quality-review.json").write_text(
        json.dumps(
            [
                {
                    "case": case["case"],
                    "sourceFacts": case.get("sourceFacts"),
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
            replay(index, chat_factory(), single, cases, root / f"run-{repeat + 1}-{member}", budget)
            metadata["runs"].append({"repeat": repeat + 1, "member": member, "order": len(metadata["runs"]) + 1})
            (root / "comparison.json").write_text(json.dumps(metadata, indent=2) + "\n")
    final = {k: index.list_documents(lib) for k, lib in libraries.items()}
    metadata["sourceUnchanged"] = snapshots == final
    (root / "comparison.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if not metadata["sourceUnchanged"]:
        raise RuntimeError("pair inventory changed across runs; comparison is confounded")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--cases", type=Path, default=Path("docs/testing/2026-10-27/casual-cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-tokens", type=int, default=48000)
    parser.add_argument("--experiment-id")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--index-ready", action="store_true")
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
        )
        return
    replay(
        IndexClient.from_env(),
        azure_chat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"]),
        Registry().get(args.agent_id),
        json.loads(args.cases.read_text()),
        args.output,
        args.context_tokens,
    )


if __name__ == "__main__":
    main()
