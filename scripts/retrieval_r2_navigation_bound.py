"""Exhaustive source-expansion feasibility bound before generating discovery summaries.

Gold is used only to evaluate every possible document choice, never to choose a
retrieval candidate. This is not a generated-summary or general navigation test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from fli.chunking import TiktokenTokenizer
from fli.structure_lab import digest
from fli.structure_representation import source_relevance
from scripts.retrieval_controls import private_path
from scripts.retrieval_r0_corpus import sha256
from scripts.retrieval_r2 import exact_rank
from scripts.retrieval_r2_navigation import inputs, named_document_hits
from scripts.structure_lab import save


def bound(source_dir, directory, output):
    controls, source, vectors, documents, tasks = inputs(source_dir, directory)
    if controls["arms"] != {
        "passages": {"passageTop": 8, "documentTop": 0},
        "named-file-control": {"passageTop": 7, "documentTop": 1},
        "document-discovery": {"passageTop": 7, "documentTop": 1},
    }:
        raise ValueError("this bound requires the frozen eight-candidate/top-one document comparison")
    if (
        controls["selectionGate"]["minimumRelativeMissingRegionReduction"] <= 0
        or controls["selectionGate"]["lostBaselineRegionIdsAllowed"] != 0
    ):
        raise ValueError("zero-gain alternatives are outside this bound")
    array = np.load(source_dir / vectors["arrayFile"], mmap_mode="r", allow_pickle=False)
    rows = [
        r for r in source["variants"][controls["selectedRepresentation"]] if r["documentId"] in controls["sourceIds"]
    ]
    row_vectors = {r["id"]: array[vectors["rows"][r["id"]]] for r in rows}
    sources, tokenizer = {d["id"]: d for d in documents}, TiktokenTokenizer()
    choices, measured_controls, named_choices = [], [], {}
    for task in tasks:
        hits = exact_rank(rows, row_vectors, array[vectors["queries"][task["case"]]], 8)
        named, named_choices[task["case"]] = named_document_hits(task["question"], documents)
        baseline = source_relevance(task, sources, hits[:7] + named, tokenizer)
        for arm, selected in (("passages", hits), ("named-file-control", hits[:7] + named)):
            measured_controls.append(source_relevance(task, sources, selected, tokenizer) | {"arm": arm})
        for doc in documents:
            candidate = {
                "id": doc["id"] + ":all-source:n0",
                "documentId": doc["id"],
                "sourceSha256": doc["sourceSha256"],
                "sourceDigest": doc["sourceDigest"],
                "sourceSpans": [{"blockId": b["id"], "start": 0, "end": len(b["text"])} for b in doc["blocks"]],
            }
            result = source_relevance(task, sources, hits[:7] + [candidate], tokenizer)
            before, after = set(baseline["deliveredRegionIds"]), set(result["deliveredRegionIds"])
            choices.append(
                {
                    "case": task["case"],
                    "selectedDocumentId": doc["id"],
                    "deliveredRegions": len(after),
                    "baselineRegions": len(before),
                    "lostRegionIds": sorted(before - after),
                    "gainedRegionIds": sorted(after - before),
                    "evidenceContextLimited": result["expandedSourceJsonTokensEstimate"] > 200000,
                }
            )
    feasible = [
        row
        for row in choices
        if not row["lostRegionIds"] and row["gainedRegionIds"] and not row["evidenceContextLimited"]
    ]
    report = {
        "schema": "fli.r2.navigation-feasibility.v1",
        "status": "complete",
        "planDigest": digest(controls),
        "controls": controls,
        "sourceArtifactDigest": source["digest"],
        "passageVectorArraySha256": vectors["arraySha256"],
        "boundCodeSha256": sha256(Path(__file__).read_bytes()),
        "choicesChecked": len(choices),
        "expectedChoices": len(documents) * len(tasks),
        "namedFileChoices": named_choices,
        "controlMeasurements": measured_controls,
        "measurements": choices,
        "summaryOrEmbeddingCalls": 0,
        "heldoutExecuted": False,
        "strictlyImprovingNoLossChoices": len(feasible),
        "decision": "stop-this-document-discovery-comparison" if not feasible else "value-still-feasible",
        "scope": "all three possible top-one document expansions on five named-file development tasks",
        "interpretation": (
            "An oracle document ranker cannot pass the fixed positive-gain/no-loss gate "
            "when no possible choice improves without loss. This is not a summary-quality result."
        ),
        "unrun": [
            "summary generation and summary embeddings",
            "summary creation/refresh/omission provider costs",
            "section discovery",
            "genuinely vague file navigation",
            "live Cosmos/Blob costs",
            "held-out confirmation",
        ],
    }
    output = private_path(output)
    if output.exists():
        raise ValueError("bound output must be new")
    save(output, report)
    print(
        json.dumps(
            {key: report[key] for key in ("status", "choicesChecked", "strictlyImprovingNoLossChoices", "decision")}
        )
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bound(private_path(args.source_dir), private_path(args.work_dir), private_path(args.output))


if __name__ == "__main__":
    main()
