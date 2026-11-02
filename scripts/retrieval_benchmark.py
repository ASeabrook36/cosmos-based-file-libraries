"""R0 controls around the existing extraction, structure lab and agent replay.

All source text, captures, gold evidence and environment bindings remain private.
This adds no application ingestion/retrieval strategy. See docs/retrieval-r0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import fcntl
import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from fli.chunking import Chunker, TiktokenTokenizer
from fli.extraction import extract
from fli.ingestion import profile
from fli.structural_enrichment import docx_structure
from fli.structure_lab import digest
from fli_demo.client import IndexClient
from scripts.compare_agent_models import code_hashes, default_chat_factory
from scripts.evaluate_agent_retrieval import prepare_output, replay, snapshot
from scripts.structure_lab import save

REPO = Path(__file__).resolve().parents[1]
MODEL = {"model": "gpt-6.1-sol", "version": "2026-09-29"}
ROLES = {"current": "current-v1", "custom": "custom-structured-v1", "microsoft": "microsoft-indexer-layout-v1"}
BUDGET = 200000


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_pack(manifest, tasks):
    documents = manifest["documents"]
    ids, names = [d["id"] for d in documents], [d["fileName"] for d in documents]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names):
        raise ValueError("source identifiers and filenames must be unique")
    if any(Path(name).name != name or not name.endswith(".docx") for name in names):
        raise ValueError("the matched R0 corpus uses plain DOCX filenames")
    if any(d["split"] not in {"development", "heldout"} for d in documents):
        raise ValueError("invalid source split")
    if len({c["case"] for c in tasks}) != len(tasks):
        raise ValueError("task identifiers must be unique")
    sources = {d["id"]: d for d in documents}
    if tasks and manifest.get("tasksSha256"):
        task_bytes = (json.dumps(tasks, indent=2, ensure_ascii=False) + "\n").encode()
        if hashlib.sha256(task_bytes).hexdigest() != manifest["tasksSha256"]:
            raise ValueError("tasks differ from frozen manifest digest")
    baseline = manifest.get("baseline", {})
    selected = baseline.get("sourceIds", [])
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(source not in sources or sources[source]["split"] != "development" for source in selected)
    ):
        raise ValueError("baseline must select distinct development sources")
    if tasks:
        by_id = {c["case"]: c for c in tasks}
        if any(key not in by_id or by_id[key]["split"] != "development" for key in baseline.get("caseIds", [])):
            raise ValueError("baseline case selection must name development tasks")
    for case in tasks:
        if case["split"] not in {"development", "heldout"}:
            raise ValueError("invalid task split")
        for fact in case.get("sourceFacts", []):
            source = sources[fact["sourceId"]]
            if fact.get("source", source["fileName"]) != source["fileName"] or any(
                region.get("sourceSha256") != source["sha256"] for region in fact.get("regions", [])
            ):
                raise ValueError("gold region source binding differs from corpus")
            if case["split"] == "development" and source["split"] == "heldout":
                raise ValueError("held-out source leaked into development")
        if case["case"] in baseline.get("caseIds", []) and any(
            fact["sourceId"] not in selected for fact in case.get("sourceFacts", [])
        ):
            raise ValueError("baseline task references an unloaded source")
    return documents


def verify_corpus(manifest, corpus):
    corpus = Path(corpus).resolve()
    if corpus.is_relative_to(REPO):
        raise ValueError("raw corpus must remain outside the repository")
    for document in manifest["documents"]:
        path = corpus / document["fileName"]
        if sha(path) != document["sha256"] or path.stat().st_size != document["sizeBytes"]:
            raise ValueError("source bytes do not match frozen manifest")


def distribution(values):
    ordered = sorted(values)
    return {
        "count": len(values),
        "total": sum(values),
        "min": min(values) if values else None,
        "p50": ordered[max(0, math.ceil(len(values) * 0.5) - 1)] if values else None,
        "p95": ordered[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None,
        "max": max(values) if values else None,
        "under100": sum(v < 100 for v in values),
    }


def normalized(value):
    return re.sub(r"\s+", " ", value).strip()


def measured_overlap(chunks, tokenizer):
    """Exact adjacent token suffix/prefix overlap; headings are reported separately."""
    values = []
    for left, right in zip(chunks, chunks[1:], strict=False):
        if left.location != right.location:
            continue

        # Repeated heading prefixes are not sentence carryover.
        def body(chunk):
            prefix = (chunk.heading + "\n") if chunk.heading else ""
            return chunk.text.removeprefix(prefix)

        a, b = tokenizer.encode(body(left)), tokenizer.encode(body(right))
        overlap = max((n for n in range(1, min(len(a), len(b), 100) + 1) if a[-n:] == b[:n]), default=0)
        values.append(overlap)
    return distribution(values)


async def local_baseline(manifest, tasks, corpus, output):
    validate_pack(manifest, tasks)
    verify_corpus(manifest, corpus)
    root = prepare_output(output)
    tokenizer = TiktokenTokenizer()
    rows = []
    # Held-out files are hash-verified only: no extraction, relevance or quality feedback.
    for document in manifest["documents"]:
        if document["split"] != "development":
            continue
        data = (Path(corpus) / document["fileName"]).read_bytes()
        native = await extract(data, filename=document["fileName"])
        if native.outcome != "ok":
            raise ValueError("native extraction did not complete")
        structured, tree = docx_structure(data, document["id"], document["fileName"])
        for role, sections in [("current", native.sections), ("custom", structured)]:
            chunks = Chunker(tokenizer).chunk(sections)
            frozen = {
                "sourceSha256": document["sha256"],
                "sections": [asdict(s) for s in sections],
                "chunks": [asdict(c) for c in chunks],
                "structure": tree if role == "custom" else None,
            }
            save(root / f"{document['id']}-{role}.json", frozen)
            body = "\n\n".join(s.text for s in sections)
            rows.append(
                {
                    "sourceId": document["id"],
                    "profile": ROLES[role],
                    "profileVersion": profile(ROLES[role])["version"],
                    "artifactSha256": digest(frozen),
                    "sections": len(sections),
                    "bodyCharacters": len(body),
                    "bodyTokensEstimate": len(tokenizer.encode(body)),
                    "chunks": distribution([c.token_count for c in chunks]),
                    "adjacentBodyOverlapTokens": measured_overlap(chunks, tokenizer),
                    "sourceSha256": document["sha256"],
                }
            )
    report = {
        "schema": "fli.r0.local-baseline.v1",
        "status": "complete",
        "manifestSha256": digest(manifest),
        "tasksSha256": digest(tasks),
        "tokenizer": "cl100k_base",
        "targetTokens": 500,
        "configuredOverlapTokens": 50,
        "overlapPolicy": "whole trailing sentences within source section",
        "overlapMeasurement": "adjacent body token suffix/prefix, excludes heading; tokenizer boundary-sensitive",
        "scope": "development-only native extraction/chunk measurement; no embedding, ranking or answer quality",
        "heldoutExecuted": False,
        "rows": rows,
        "codeSha256": benchmark_code_hashes(),
    }
    save(root / "receipt.json", report)
    save(root / "run-status.json", {"status": "complete", "sourceRepresentations": len(rows)})
    return report


def benchmark_code_hashes():
    return {**code_hashes(), "scripts/retrieval_benchmark.py": sha(Path(__file__))}


def validate_sources(frozen, config, manifest):
    expected = sorted(
        (d["fileName"], d["sha256"]) for d in manifest["documents"] if d["id"] in manifest["baseline"]["sourceIds"]
    )
    metadata_keys = ("author", "periodStart", "periodEnd", "tags")
    embeddings = []
    for role, library in config["libraries"].items():
        captured = frozen["libraries"][library]
        documents = captured["documents"]
        if sorted((d["fileName"], d["sha256"]) for d in documents) != expected:
            raise ValueError("comparison corpus differs from frozen development source bytes")
        recipe = captured["library"].get("ingestionProfile", {})
        if recipe.get("id") != ROLES[role]:
            raise ValueError("library profile differs from frozen comparison role")
        expected_recipe = profile(ROLES[role])
        for key in ("version", "provider", "extraction", "extractionOnly", "chunking", "enrichment", "workflowVersion"):
            if recipe.get(key) != expected_recipe[key]:
                raise ValueError("library recipe differs from frozen baseline profile")
        if manifest.get("controls"):
            embedding = recipe.get("embedding", {})
            query = manifest["controls"]["query"]
            if (
                embedding.get("model") != query["embeddingModel"]
                or embedding.get("dimensions") != query["dimensions"]
                or not embedding.get("modelRevision")
            ):
                raise ValueError("embedding model/revision/dimensions must be pinned")
            embeddings.append(embedding)
        for document in documents:
            source = next(d for d in manifest["documents"] if d["fileName"] == document["fileName"])
            if (
                document.get("status") != "ready"
                or document.get("pendingGeneration")
                or not document.get("activeGeneration")
            ):
                raise ValueError("all frozen sources must be ready without a pending generation")
            if document["activeGeneration"] != source.get("expectedInitialGeneration", 1):
                raise ValueError("source generation differs from frozen initial baseline")
            for key in metadata_keys:
                if key in source.get("metadata", {}):
                    actual = document.get(key)
                    wanted = source["metadata"][key]
                    if key == "tags":
                        actual = sorted((v["key"], v["value"]) for v in actual or [])
                        wanted = sorted((v["key"], v["value"]) for v in wanted)
                    if actual != wanted:
                        raise ValueError("comparison metadata differs from manifest")
    if embeddings and any(e != embeddings[0] for e in embeddings[1:]):
        raise ValueError("embedding controls differ across comparison members")


def validate_deployment(receipt):
    actual = receipt.get("properties", {})
    model = actual.get("model", {})
    if (
        model.get("name") != MODEL["model"]
        or model.get("version") != MODEL["version"]
        or actual.get("versionUpgradeOption") != "NoAutoUpgrade"
        or actual.get("provisioningState") != "Succeeded"
    ):
        raise ValueError("actual deployment does not match the fixed answering model")


def run_replay(
    index,
    config,
    manifest,
    tasks,
    output,
    *,
    case_ids=None,
    repeats=2,
    chat_factory=default_chat_factory,
    replay_fn=replay,
    hash_fn=benchmark_code_hashes,
):
    config, manifest, tasks = copy.deepcopy((config, manifest, tasks))
    validate_pack(manifest, tasks)
    if set(config) != {"model", "agent", "libraries"}:
        raise ValueError("private config requires exactly model, agent, libraries; credentials stay in environment")
    if set(config["model"]) != {"deployment", "model", "version"} or any(
        config["model"].get(k) != v for k, v in MODEL.items()
    ):
        raise ValueError("R0 answering model and revision are fixed")
    if set(config["libraries"]) != set(ROLES) or len(set(config["libraries"].values())) != len(ROLES):
        raise ValueError("three distinct baseline library roles required")
    if not config["agent"].get("id") or not config["agent"].get("instructions"):
        raise ValueError("agent identity and instructions required")
    if type(repeats) is not int or not 1 <= repeats <= 3:
        raise ValueError("bounded replay requires one to three repeats")
    chosen = (
        [c for c in tasks if c["case"] in case_ids] if case_ids else [c for c in tasks if c["split"] == "development"]
    )
    if not chosen or (case_ids and set(case_ids) != {c["case"] for c in chosen}):
        raise ValueError("unknown or empty case selection")
    if any(c["split"] != "development" for c in chosen):
        raise ValueError("R0 replay cannot consume held-out tasks")
    if any(f["sourceId"] not in manifest["baseline"]["sourceIds"] for c in chosen for f in c.get("sourceFacts", [])):
        raise ValueError("selected task references an unloaded source")
    root = prepare_output(output)
    agent = {**config["agent"], "libraryIds": list(config["libraries"].values())}
    try:
        before, initial_code = snapshot(index, agent), hash_fn()
        validate_sources(before, config, manifest)
    except (Exception, KeyboardInterrupt) as error:
        save(root / "run-status.json", {"status": "failed", "completedTurns": 0, "errorType": type(error).__name__})
        raise
    schedule = [
        {"role": role, "repeat": n}
        for n in range(1, repeats + 1)
        for role in (list(ROLES) if n % 2 else list(reversed(ROLES)))
    ]
    controls = {
        "schema": "fli.r0.replay.v1",
        "model": config["model"],
        "contextAllowancePerRun": BUDGET,
        "reasoningEffort": "medium",
        "maxOutputTokens": 8000,
        "maxAttempts": 3,
        "transport": "responses",
        "store": False,
        "automaticTruncation": False,
        "manifestSha256": digest(manifest),
        "tasksSha256": digest(tasks),
        "codeSha256": initial_code,
        "agent": config["agent"],
        "cases": chosen,
        "schedule": schedule,
        "mode": "functional-smoke; no ANN quality or latency/cost winner",
        "heldoutExecuted": False,
    }
    save(root / "controls.json", controls)
    save(root / "source-snapshot.json", before)
    completed, active = 0, None
    try:
        for cell in schedule:
            if snapshot(index, agent) != before or hash_fn() != initial_code:
                raise RuntimeError("source or code drift; comparison confounded")
            active = root / f"r{cell['repeat']}-{cell['role']}"
            result = replay_fn(
                index,
                chat_factory(config["model"], "responses"),
                {**config["agent"], "libraryIds": [config["libraries"][cell["role"]]]},
                copy.deepcopy(chosen),
                active,
                budget=BUDGET,
            )
            completed += len(result)
            active = None
        if snapshot(index, agent) != before or hash_fn() != initial_code:
            raise RuntimeError("source or code drift; comparison confounded")
    except (Exception, KeyboardInterrupt) as error:
        partial = (
            read(active / "run-status.json").get("completedTurns", 0)
            if active and (active / "run-status.json").exists()
            else 0
        )
        save(
            root / "run-status.json",
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                "errorType": type(error).__name__,
                "completedTurns": completed + partial,
                "unfinishedUsage": "see cell receipt; missing counters unavailable",
            },
        )
        raise
    status = {
        "status": "complete",
        "completedTurns": completed,
        "snapshotsUnchanged": True,
        "contextAllowancePerRun": BUDGET,
        "heldoutExecuted": False,
    }
    save(root / "run-status.json", status)
    return status


def ingest(index, manifest, corpus, output, wait_seconds=900):
    """New isolated libraries only. Pair retries are idempotent; ambiguous current writes stop."""
    validate_pack(manifest, [])
    verify_corpus(manifest, corpus)
    root = Path(output).resolve()
    if root.is_relative_to(REPO):
        raise ValueError("runtime artifacts must remain outside repository")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".ingest.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("another ingestion owns this private run") from error
        return _ingest(index, manifest, corpus, root, wait_seconds)


def _ingest(index, manifest, corpus, root, wait_seconds):
    state_path = root / "ingest-state.json"
    state = (
        read(state_path)
        if state_path.exists()
        else {
            "manifestDocumentsSha256": digest(manifest["documents"]),
            "sourceIds": manifest["baseline"]["sourceIds"],
            "experimentId": str(uuid.uuid4()),
            "sources": {},
        }
    )
    if (
        state["manifestDocumentsSha256"] != digest(manifest["documents"])
        or state["sourceIds"] != manifest["baseline"]["sourceIds"]
    ):
        raise ValueError("ingestion inputs changed; use a new isolated run")
    save(state_path, state)
    if "current" not in state:
        if state.get("currentAttempted"):
            raise ValueError("ambiguous current-library creation: inspect private receipt before recovery")
        state["currentAttempted"] = True
        save(state_path, state)
        state["current"] = index.create_library("shared", "R0 policy pilot", "Public policy consultation sources.")
        save(state_path, state)
    state["pair"] = index.create_pair(state["experimentId"], "R0 policy pilot", "Public policy consultation sources.")
    save(state_path, state)
    libraries = {
        "current": state["current"]["id"],
        **{role: member["libraryId"] for role, member in state["pair"]["members"].items()},
    }
    for source in manifest["documents"]:
        if source["id"] not in state["sourceIds"]:
            continue
        if source["split"] != "development":
            raise ValueError("held-out ingestion forbidden in R0 baseline")
        entry = state["sources"].setdefault(source["id"], {"sourceId": str(uuid.uuid4())})
        save(state_path, state)
        data = (Path(corpus) / source["fileName"]).read_bytes()
        upload = {
            "files": {
                "file": (
                    source["fileName"],
                    data,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            "data": {"metadata": json.dumps(source.get("metadata", {}))},
        }
        entry["pair"] = index._call(
            "POST", f"/v1/experiments/{state['experimentId']}/sources/{entry['sourceId']}", **upload
        )
        save(state_path, state)
        if "currentDocumentId" not in entry:
            if entry.get("currentAttempted"):
                raise ValueError("ambiguous current upload: inspect private receipt before recovery")
            entry["currentAttempted"] = True
            save(state_path, state)
            entry["currentDocumentId"] = index._call(
                "POST", f"/v1/libraries/{libraries['current']}/documents", **upload
            )["documentId"]
            save(state_path, state)
        print(f"{source['id']}: matched bytes/metadata queued", flush=True)
    until = time.monotonic() + wait_seconds
    while True:
        documents = {role: index.list_documents(library) for role, library in libraries.items()}
        save(root / "documents.json", documents)
        if any(d["status"] == "failed" for docs in documents.values() for d in docs):
            raise RuntimeError("ingestion failed; preserve receipts, do not silently change recipe")
        if all(
            len(docs) == len(state["sourceIds"])
            and all(d["status"] == "ready" and not d.get("pendingGeneration") for d in docs)
            for docs in documents.values()
        ):
            save(root / "libraries.json", libraries)
            return libraries
        if time.monotonic() >= until:
            raise TimeoutError("bounded ingestion wait expired; resume same private state later")
        time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "local", "replay", "ingest"])
    parser.add_argument("--manifest", type=Path, default=REPO / "docs/testing/retrieval-r0/manifest.json")
    parser.add_argument("--tasks", type=Path, default=REPO / "docs/testing/retrieval-r0/tasks.json")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--deployment-receipt", type=Path, help="private fresh management-plane model receipt")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    manifest = read(args.manifest)
    if args.action == "ingest":
        if not args.corpus or not args.output:
            parser.error("ingest requires --corpus and --output")
        ingest(IndexClient.from_env(), manifest, args.corpus, args.output)
        print("Isolated baseline sources ready")
        return
    tasks = read(args.tasks)
    validate_pack(manifest, tasks)
    if args.action == "validate":
        if args.corpus:
            verify_corpus(manifest, args.corpus)
        print(json.dumps({"valid": True, "documents": len(manifest["documents"]), "tasks": len(tasks)}))
    elif args.action == "local":
        if not args.corpus or not args.output:
            parser.error("local requires --corpus and --output")
        report = asyncio.run(local_baseline(manifest, tasks, args.corpus, args.output))
        print(json.dumps({"status": report["status"], "representations": len(report["rows"])}))
    else:
        if not args.config or not args.output or not args.deployment_receipt:
            parser.error("replay requires --config, --deployment-receipt and --output")
        if args.config.resolve().is_relative_to(REPO):
            parser.error("environment bindings must remain outside repository")
        if args.deployment_receipt.resolve().is_relative_to(REPO):
            parser.error("deployment identity must remain outside repository")
        validate_deployment(read(args.deployment_receipt))
        print(
            json.dumps(
                run_replay(
                    IndexClient.from_env(),
                    read(args.config),
                    manifest,
                    tasks,
                    args.output,
                    case_ids=args.cases,
                    repeats=args.repeats,
                )
            )
        )


if __name__ == "__main__":
    main()
