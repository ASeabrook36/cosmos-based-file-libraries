"""Bounded document-discovery follow-on using unchanged R2 passage vectors.

This is a controlled extension of the existing structure-lab Summarizer and
source expansion, not an API or ingestion recipe. Run after the passage decision.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import time
from pathlib import Path

from fli.chunking import TiktokenTokenizer
from fli.proposals import ChatReply
from fli.structure_lab import Summarizer, digest
from fli.structure_representation import VARIANTS, source_relevance
from scripts.compare_agent_models import default_chat_factory
from scripts.retrieval_controls import deployment_binding, private_path
from scripts.retrieval_r0_corpus import sha256
from scripts.retrieval_r2 import deployment_identity, exact_rank, load_source
from scripts.structure_lab import Cache, TokenCache, VectorCache, cached_vectors, save

CASES = ("r0-17", "r0-22", "r0-25", "r0-27", "r0-30")
SOURCES = ("d06", "d07", "d08")


def read(path):
    return json.loads(Path(path).read_text())


def plan(source_dir: Path, directory: Path):
    if (directory / "navigation-plan.json").exists():
        raise ValueError("navigation plan requires a fresh private directory")
    source = load_source(source_dir)
    prior = read(source_dir / "r2-evaluation.json")
    if prior["sourceArtifactDigest"] != source["digest"] or prior["status"] != "complete":
        raise ValueError("a completed passage comparison is required first")
    selected = VARIANTS[1] if prior["candidatePassedPredeclaredGate"] else VARIANTS[0]
    vectors = read(source_dir / "r2-vectors.json")
    controls = {
        "schema": "fli.r2.navigation-plan.v1",
        "sourceArtifactDigest": source["digest"],
        "passageVectorArraySha256": vectors["arraySha256"],
        "passageDecisionDigest": digest(prior),
        "selectedRepresentation": selected,
        "sourceIds": list(SOURCES),
        "caseIds": list(CASES),
        "scope": "three enforcement development sources; no long-document or held-out discovery claim",
        "arms": {
            "passages": {"passageTop": 8, "documentTop": 0},
            "document-discovery": {"passageTop": 7, "documentTop": 1},
        },
        "changedVariable": "replace the eighth passage candidate with one generated document-discovery candidate",
        "readExpansion": (
            "same original blocks for passage hits; whole original document for document hit; deduplicated"
        ),
        "summaryModel": {
            "model": "gpt-6.1-sol",
            "version": "2026-09-29",
            "reasoningEffort": "medium",
            "transport": "responses",
            "maxOutputTokens": 8000,
            "maxAttempts": 3,
        },
        "summaryInputBudget": 12000,
        "summaryOutputBudget": 220,
        "summaryPolicy": "existing Summarizer.reduce over all canonical blocks; never truncate source",
        "embeddingModel": "text-embedding-3-large",
        "embeddingRevision": "1",
        "dimensions": 3072,
        "evidenceOnlyTokenAllowance": 200000,
        "heldoutExecuted": False,
        "selectionGate": {
            "minimumRelativeMissingRegionReduction": 0.2,
            "lostBaselineRegionIdsAllowed": 0,
            "contextLimitedCasesAllowed": 0,
        },
        "costPolicy": (
            "report added source JSON tokens and creation/cache/omission counters; "
            "source gain can justify added cost only at integration review"
        ),
        "codeSha256": sha256(Path(__file__).read_bytes()),
    }
    save(directory / "navigation-plan.json", controls)
    print(json.dumps({"status": "planned", "selectedRepresentation": selected, "documents": 3, "tasks": 5}))


def inputs(source_dir, directory):
    controls = read(directory / "navigation-plan.json")
    source = load_source(source_dir)
    if (
        controls["codeSha256"] != sha256(Path(__file__).read_bytes())
        or source["digest"] != controls["sourceArtifactDigest"]
    ):
        raise ValueError("navigation code or source changed since predeclaration")
    if digest(read(source_dir / "r2-evaluation.json")) != controls["passageDecisionDigest"]:
        raise ValueError("passage decision changed after navigation predeclaration")
    vectors = read(source_dir / "r2-vectors.json")
    expected = vectors.pop("digest")
    if digest(vectors) != expected or vectors["arraySha256"] != controls["passageVectorArraySha256"]:
        raise ValueError("passage vectors changed")
    if sha256((source_dir / vectors["arrayFile"]).read_bytes()) != vectors["arraySha256"]:
        raise ValueError("passage vector bytes changed")
    documents = [d for d in source["bundle"]["documents"] if d["id"] in SOURCES]
    tasks = [t for t in source["tasks"] if t["case"] in CASES]
    if len(documents) != len(SOURCES) or len(tasks) != len(CASES):
        raise ValueError("navigation source/task matrix incomplete")
    return controls, source, vectors, documents, tasks


def summary_binding(receipt, endpoint, deployment):
    return deployment_binding(
        receipt,
        configured_endpoint=endpoint,
        configured_deployment=deployment,
        model="gpt-6.1-sol",
        revision="2026-09-29",
    )


async def freeze(args, source_dir, directory):
    import httpx
    from azure.identity.aio import DefaultAzureCredential

    from fli.embedding import AzureOpenAIEmbedder

    controls, source, _, documents, _ = inputs(source_dir, directory)
    if (directory / "navigation-frozen.json").exists():
        raise ValueError("completed navigation vectors are immutable")
    endpoint = os.environ["AOAI_ENDPOINT"]
    chat_deployment = os.environ["AOAI_CHAT_DEPLOYMENT"]
    embedding_deployment = os.environ["AOAI_EMBEDDING_DEPLOYMENT"]
    model_binding = summary_binding(read(private_path(args.model_receipt)), endpoint, chat_deployment)
    embedding_binding = deployment_identity(read(private_path(args.embedding_receipt)), endpoint, embedding_deployment)
    calls, cache = [], Cache(directory / "summary-cache.json")
    started, status = time.perf_counter(), "interrupted"
    usage = None

    class ResponsesSummary:
        async def complete_json(self, messages):
            chat = default_chat_factory({"deployment": chat_deployment}, "responses")
            try:
                reply = await asyncio.to_thread(chat, messages, [])
                data = json.loads(reply.content)
                calls.append({"status": "complete", "usage": reply.usage, "timing": reply.timing})
                save(directory / "summary-calls.json", calls)
                return ChatReply(
                    data,
                    reply.usage.get("prompt_tokens", 0),
                    reply.usage.get("completion_tokens", 0),
                )
            except Exception as exc:
                calls.append(
                    {
                        "status": "failed",
                        "errorType": type(exc).__name__,
                        "usage": getattr(exc, "usage", getattr(locals().get("reply"), "usage", None)),
                        "timing": getattr(exc, "timing", getattr(locals().get("reply"), "timing", None)),
                    }
                )
                save(directory / "summary-calls.json", calls)
                raise

    summarizer = Summarizer(
        ResponsesSummary(),
        TiktokenTokenizer(),
        model_binding["bindingSha256"],
        cache=cache,
        input_tokens=12000,
        summary_tokens=220,
    )
    credential = DefaultAzureCredential()
    try:
        summaries, source_inputs = [], []
        for doc in documents:
            full_source = "".join(b["text"] for b in doc["blocks"])
            if digest(full_source) != doc["sourceDigest"]:
                raise ValueError("summary source is not the entire frozen canonical document")
            source_inputs.append(
                {
                    "sourceId": doc["id"],
                    "sourceBlocks": len(doc["blocks"]),
                    "sourceCharacters": len(full_source),
                    "sourceDigest": digest(full_source),
                    "sourceTokensEstimate": len(summarizer.tokenizer.encode(full_source)),
                    "inputCoverage": "all canonical blocks in original order; no clipping",
                }
            )
            text = await summarizer.reduce(
                {"title": doc["title"], "level": "document", "genre": doc["genre"]},
                [full_source],
                doc["canonicalTreeDigest"],
            )
            summaries.append(
                {
                    "id": doc["id"] + ":summary:n0",
                    "documentId": doc["id"],
                    "nodeId": "n0",
                    "generated": True,
                    "level": "document",
                    "recordKind": "summary",
                    "text": text,
                    "sourceSha256": doc["sourceSha256"],
                    "sourceDigest": doc["sourceDigest"],
                    "sourceSpans": [{"blockId": b["id"], "start": 0, "end": len(b["text"])} for b in doc["blocks"]],
                }
            )
        calls_before_cache = summarizer.calls
        for doc in documents:
            await summarizer.reduce(
                {"title": doc["title"], "level": "document", "genre": doc["genre"]},
                ["".join(b["text"] for b in doc["blocks"])],
                doc["canonicalTreeDigest"],
            )
        async with httpx.AsyncClient() as http:
            embedder = AzureOpenAIEmbedder(endpoint, embedding_deployment, TokenCache(credential), http)
            usage = embedder.usage
            vcache = VectorCache(directory / "embedding-cache.sqlite3")
            try:
                vectors = await cached_vectors([row["text"] for row in summaries], embedder, vcache, embedding_binding)
            finally:
                vcache.close()
        before_omission_calls = summarizer.calls
        original_inventory = {
            row["documentId"]: {"row": row, "vector": vector} for row, vector in zip(summaries, vectors, strict=True)
        }
        omitted_id = documents[-1]["id"]
        remaining_inventory = {key: value for key, value in original_inventory.items() if key != omitted_id}
        removed_ids = sorted(set(original_inventory) - set(remaining_inventory))
        retained_unchanged = all(value == original_inventory[key] for key, value in remaining_inventory.items())
        if removed_ids != [omitted_id] or not retained_unchanged:
            raise ValueError("immutable navigation omission changed retained source/vector bindings")
        omission = {
            "omittedDocuments": len(removed_ids),
            "removedSummaryRows": len(original_inventory) - len(remaining_inventory),
            "remainingSummaryRows": len(remaining_inventory),
            "additionalModelCalls": summarizer.calls - before_omission_calls,
            "retainedSummaryVectorsUnchanged": retained_unchanged,
            "originalInventoryDigest": digest(original_inventory),
            "remainingInventoryDigest": digest(remaining_inventory),
            "scope": "executed local immutable inventory filter; no cloud deletion or mutable publication",
        }
        save(
            directory / "navigation-omission-private.json",
            {"removedDocumentIds": removed_ids, "remaining": remaining_inventory, "receipt": omission},
        )
        frozen = {
            "schema": "fli.r2.navigation-frozen.v1",
            "planDigest": digest(controls),
            "sourceArtifactDigest": source["digest"],
            "summaries": summaries,
            "vectors": {row["id"]: vector for row, vector in zip(summaries, vectors, strict=True)},
            "summaryBinding": model_binding,
            "embeddingIdentity": embedding_binding,
            "privateBinding": {
                "endpoint": endpoint,
                "summaryDeployment": chat_deployment,
                "embeddingDeployment": embedding_deployment,
            },
            "unchangedRefreshAdditionalModelCalls": summarizer.calls - calls_before_cache,
            "summaryCreationCalls": calls_before_cache,
            "summarySourceInputs": source_inputs,
            "omission": omission,
        }
        frozen["digest"] = digest(frozen)
        save(directory / "navigation-frozen.json", frozen)
        status = "complete"
    finally:
        from dataclasses import asdict

        await credential.close()
        save(
            directory / f"navigation-attempt-{time.time_ns()}.json",
            {
                "status": status,
                "summaryCalls": calls,
                "embedding": asdict(usage) if usage else None,
                "elapsedMs": (time.perf_counter() - started) * 1000,
                "unknownFailedCallTokens": "unavailable",
                "heldoutExecuted": False,
            },
        )
    print(json.dumps({"status": status, "summaryCalls": summarizer.calls, "summaryCacheHits": summarizer.hits}))


def evaluate(args, source_dir, directory):
    import numpy as np

    controls, source, vectors, documents, tasks = inputs(source_dir, directory)
    frozen = read(directory / "navigation-frozen.json")
    expected = frozen.pop("digest")
    if digest(frozen) != expected or frozen["planDigest"] != digest(controls):
        raise ValueError("navigation artifact changed")
    binding = frozen["privateBinding"]
    if (
        summary_binding(read(private_path(args.model_receipt)), binding["endpoint"], binding["summaryDeployment"])
        != frozen["summaryBinding"]
    ):
        raise ValueError("summary model changed")
    if (
        deployment_identity(
            read(private_path(args.embedding_receipt)), binding["endpoint"], binding["embeddingDeployment"]
        )
        != frozen["embeddingIdentity"]
    ):
        raise ValueError("embedding model changed")
    array = np.load(source_dir / vectors["arrayFile"], mmap_mode="r", allow_pickle=False)
    rows = [r for r in source["variants"][controls["selectedRepresentation"]] if r["documentId"] in SOURCES]
    passage_vectors = {r["id"]: array[vectors["rows"][r["id"]]] for r in rows}
    docs, tok = {d["id"]: d for d in documents}, TiktokenTokenizer()
    measurements, lost, gains = [], {}, {}
    for task in tasks:
        query = array[vectors["queries"][task["case"]]]
        passage_hits = exact_rank(rows, passage_vectors, query, 8)
        summary_hits = exact_rank(frozen["summaries"], frozen["vectors"], query, 1)
        pair = []
        for arm, hits in (("passages", passage_hits), ("document-discovery", passage_hits[:7] + summary_hits)):
            result = source_relevance(task, docs, hits, tok)
            pair.append(result)
            measurements.append(
                result
                | {
                    "arm": arm,
                    "resultIds": [r["id"] for r in hits],
                    "evidenceContextLimited": result["expandedSourceJsonTokensEstimate"] > 200000,
                }
            )
        a, b = [set(r["deliveredRegionIds"]) for r in pair]
        if a - b:
            lost[task["case"]] = sorted(a - b)
        if b - a:
            gains[task["case"]] = sorted(b - a)
    missing = {
        arm: sum(len(m["missingRegionIds"]) for m in measurements if m["arm"] == arm) for arm in controls["arms"]
    }
    reduction = 1 - missing["document-discovery"] / missing["passages"] if missing["passages"] else 0
    limited = sorted({m["case"] for m in measurements if m["evidenceContextLimited"]})
    passed = not lost and not limited and reduction >= 0.2
    report = {
        "schema": "fli.r2.navigation-result.v1",
        "status": "complete",
        "controls": controls,
        "navigationArtifactDigest": expected,
        "measurements": measurements,
        "lostRegionIdsByCase": lost,
        "gainedRegionIdsByCase": gains,
        "missingRegions": missing,
        "relativeMissingRegionReduction": reduction,
        "evidenceContextLimitedCases": limited,
        "candidatePassedPredeclaredGate": passed,
        "recommendation": "bounded-document-discovery-candidate" if passed else "retain-passages-only",
        "summaryCreationCalls": frozen["summaryCreationCalls"],
        "unchangedRefreshAdditionalModelCalls": frozen["unchangedRefreshAdditionalModelCalls"],
        "omission": frozen["omission"],
        "summarySourceInputs": frozen["summarySourceInputs"],
        "changedSourceRefresh": "unmeasured; recompute affected document summary",
        "limitations": [
            "three development sources/five section-comparison tasks, not broad vague navigation",
            "source delivery only; answering quality/actual provider evidence tokens unmeasured",
            "source reads expanded locally; Cosmos/Blob write/read charges and latency unavailable",
            "source gain may increase read/context cost; review before integration",
        ],
    }
    save(directory / "navigation-result.json", report)
    print(
        json.dumps(
            {k: report[k] for k in ("status", "missingRegions", "relativeMissingRegionReduction", "recommendation")}
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "freeze", "evaluate"))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model-receipt", type=Path)
    parser.add_argument("--embedding-receipt", type=Path)
    args = parser.parse_args()
    source_dir, directory = private_path(args.source_dir), private_path(args.work_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / ".navigation-lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "plan":
            plan(source_dir, directory)
        elif args.command == "freeze":
            asyncio.run(freeze(args, source_dir, directory))
        else:
            evaluate(args, source_dir, directory)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "errorType": type(exc).__name__}))
        raise SystemExit(1) from None
