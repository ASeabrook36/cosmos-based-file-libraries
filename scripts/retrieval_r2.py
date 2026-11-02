"""R2 controlled representation operations, dispatched by scripts.structure_lab.

Raw artifacts and immutable vectors remain private. This reuses the lab's source,
cache, embedding and snapshot helpers; it does not create an ingestion service.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from fli.chunking import TiktokenTokenizer
from fli.query import to_float16_query
from fli.structure_lab import VERSION, digest
from fli.structure_representation import VARIANTS, docx_document, passage_rows, source_relevance, verify_rows
from scripts.retrieval_benchmark import distribution
from scripts.retrieval_controls import deployment_binding
from scripts.retrieval_r0_corpus import normalize, private_path, sha256, validate_pack
from scripts.structure_lab import TokenCache, VectorCache, cached_vectors, save

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "docs/testing/retrieval-r0"
R0_MANIFEST_SHA = "ef9e8ae4037903e8c18a1db09dbb7410030be53ab6c35e6549218ae6803ed1cf"
R0_TASK_SHA = "997a7090c3cca5dacef760a5d0a241eeed4f90168ae427877ec70b4e51deaab5"


def code_hashes():
    paths = [
        "src/fli/structural_enrichment.py",
        "src/fli/structure_representation.py",
        "src/fli/structure_lab.py",
        "src/fli/chunking.py",
        "src/fli/query.py",
        "src/fli/embedding.py",
        "scripts/retrieval_r2.py",
        "scripts/structure_lab.py",
        "scripts/retrieval_r0_corpus.py",
        "scripts/retrieval_controls.py",
    ]
    return {path: sha256((REPO / path).read_bytes()) for path in paths}


def frozen_pack():
    manifest_bytes, task_bytes = [(PACK / f).read_bytes() for f in ("manifest.json", "tasks.json")]
    if sha256(manifest_bytes) != R0_MANIFEST_SHA or sha256(task_bytes) != R0_TASK_SHA:
        raise ValueError("R0 frozen pack changed")
    manifest, tasks = json.loads(manifest_bytes), json.loads(task_bytes)
    validate_pack(manifest, tasks)
    return manifest, [t for t in tasks if t["split"] == "development" and t["sourceFacts"]]


def verify_gold(documents: list[dict], tasks: list[dict]):
    """Recompute original-region hashes from the fixed source sidecar, independently of rows."""
    regions = {}
    for doc in documents:
        for block in doc["blocks"]:
            for region in block["originalRegions"]:
                key = doc["id"], digest(region["locator"])
                text = block["text"][region["start"] : region["end"]]
                value = (sha256(normalize(text).encode()), doc["sourceSha256"])
                if key in regions and regions[key] != value:
                    raise ValueError("conflicting original coordinate")
                regions[key] = value
    checked = set()
    for task in tasks:
        for fact in task["sourceFacts"]:
            for region in fact["regions"]:
                locator = {k: region[k] for k in ("kind", "index", "table", "row", "column") if k in region}
                if regions.get((fact["sourceId"], digest(locator))) != (
                    region["normalizedTextSha256"],
                    region["sourceSha256"],
                ):
                    raise ValueError("source gold differs from original region")
                checked.add(region["id"])
    return len(checked)


def local(args, directory: Path):
    manifest, tasks = frozen_pack()
    if (directory / "r2-source.json").exists():
        raise ValueError("local freeze requires a fresh work directory")
    corpus = private_path(args.source_dir)
    documents = []
    for source in manifest["documents"]:
        if source["split"] != "development":
            continue
        data = (corpus / source["fileName"]).read_bytes()
        if len(data) != source["sizeBytes"] or sha256(data) != source["sha256"]:
            raise ValueError("development original changed")
        doc = docx_document(data, source["id"], source["fileName"])
        doc["metadata"] = source["metadata"]
        documents.append(doc)
    gold_count = verify_gold(documents, tasks)
    tokenizer = TiktokenTokenizer()
    library_id = "structure-lab-" + uuid.uuid4().hex
    variants = {name: [r for d in documents for r in passage_rows(d, library_id, name, tokenizer)] for name in VARIANTS}
    plan = {
        "schema": "fli.r2.controls.v1",
        "baseCommit": "a449dc01e5acde6391bfc1aa50ca07670d54097d",
        "manifestSha256": R0_MANIFEST_SHA,
        "tasksSha256": R0_TASK_SHA,
        "caseIds": [t["case"] for t in tasks],
        "sourceIds": [d["id"] for d in documents],
        "variants": list(VARIANTS),
        "targetTokens": 500,
        "overlapTokens": 0,
        "tokenizer": tokenizer.name,
        "embeddingModel": "text-embedding-3-large",
        "dimensions": 3072,
        "dataType": "float16",
        "top": 8,
        "ranking": "independent exact cosine; float16 conversion as production helper",
        "readExpansion": "complete canonical source blocks addressed by passage spans, source-order deduplicated",
        "summaryVectors": False,
        "heldoutExecuted": False,
        "selectionGate": {
            "minimumRelativeVectorCountReduction": 0.2,
            "perTaskGoldRegionRegressionAllowed": 0,
            "criterion": "baseline delivered-region set must be a subset of candidate delivered-region set",
        },
        "qualityScope": "direct original-source delivery; no answer-model or general accuracy claim",
        "physicalPerformance": "unmeasured; no Cosmos/index changes",
        "evidenceOnlyTokenAllowance": 200000,
        "contextPolicy": (
            "Source JSON above 200k makes selection inconclusive; "
            "this evidence-only bound excludes answering prompt/output reserves"
        ),
        "codeSha256": code_hashes(),
    }
    artifact = {
        "bundle": {"version": VERSION, "libraryId": library_id, "documents": documents},
        "controls": plan,
        "tasks": tasks,
        "variants": variants,
    }
    artifact["digest"] = digest(artifact)
    save(directory / "r2-source.json", artifact)
    stats = []
    unique_texts = set()
    for variant, rows in variants.items():
        unique_texts.update(r["text"] for r in rows)
        for doc in documents:
            selected = [r for r in rows if r["documentId"] == doc["id"]]
            stats.append(
                {
                    "variant": variant,
                    "sourceId": doc["id"],
                    "sourceSha256": doc["sourceSha256"],
                    "canonicalTreeDigest": doc["canonicalTreeDigest"],
                    "passageTokensEstimate": distribution([r["tokenCount"] for r in selected]),
                    "sourceBlocks": len(doc["blocks"]),
                    "nodes": len(doc["nodes"]),
                    "tableBlocks": sum(b["kind"] == "table" for b in doc["blocks"]),
                    "rowsDigest": digest(selected),
                }
            )
    unique_texts.update(t["question"] for t in tasks)
    report = {
        "schema": "fli.r2.local.v1",
        "status": "complete",
        "controls": plan,
        "sourceArtifactDigest": artifact["digest"],
        "originalRegionsVerified": gold_count,
        "rows": stats,
        "uniqueEmbeddingTexts": len(unique_texts),
        "uniqueEmbeddingTokensEstimate": sum(len(tokenizer.encode(t)) for t in unique_texts),
        "rowsByVariant": {v: len(r) for v, r in variants.items()},
    }
    save(directory / "r2-local.json", report)
    print(
        json.dumps(
            {k: report[k] for k in ("status", "rowsByVariant", "uniqueEmbeddingTexts", "uniqueEmbeddingTokensEstimate")}
        )
    )


def load_source(directory: Path):
    value = json.loads((directory / "r2-source.json").read_text())
    expected = value.pop("digest")
    if digest(value) != expected:
        raise ValueError("source artifact changed")
    value["digest"] = expected
    if value["controls"]["codeSha256"] != code_hashes():
        raise ValueError("implementation changed since freeze; create a new named comparison")
    docs = {d["id"]: d for d in value["bundle"]["documents"]}
    if set(docs) != set(value["controls"]["sourceIds"]) or any(t["split"] != "development" for t in value["tasks"]):
        raise ValueError("source split changed")
    for rows in value["variants"].values():
        for did, doc in docs.items():
            verify_rows(doc, [r for r in rows if r["documentId"] == did])
    verify_gold(list(docs.values()), value["tasks"])
    return value


def deployment_identity(receipt: dict, endpoint: str, deployment: str) -> str:
    binding = deployment_binding(
        receipt,
        configured_endpoint=endpoint,
        configured_deployment=deployment,
        model="text-embedding-3-large",
        revision="1",
    )
    return binding["bindingSha256"]


async def embed(args, directory):
    import httpx
    from azure.identity.aio import DefaultAzureCredential

    from fli.embedding import AzureOpenAIEmbedder

    artifact = load_source(directory)
    endpoint, deployment = os.environ["AOAI_ENDPOINT"], os.environ["AOAI_EMBEDDING_DEPLOYMENT"]
    receipt = json.loads(private_path(args.deployment_receipt).read_text())
    identity = deployment_identity(receipt, endpoint, deployment)
    if (directory / "r2-vectors.json").exists():
        raise ValueError("completed vectors are immutable")
    started = time.perf_counter()
    credential = DefaultAzureCredential()
    usage, status = None, "interrupted"
    token_wait_ms = 0.0

    async def counted_sleep(seconds):
        nonlocal token_wait_ms
        token_wait_ms += seconds * 1000
        await asyncio.sleep(seconds)

    try:
        async with httpx.AsyncClient() as http:
            embedder = AzureOpenAIEmbedder(endpoint, deployment, TokenCache(credential), http, sleep=counted_sleep)
            usage = embedder.usage
            import numpy as np

            cache = VectorCache(directory / "embedding-cache.sqlite3")
            before = len(cache)
            rows = [r for variant in VARIANTS for r in artifact["variants"][variant]]
            texts = [r["text"] for r in rows] + [t["question"] for t in artifact["tasks"]]
            partial = directory / "r2-vectors-partial.npy"
            values = np.lib.format.open_memmap(partial, mode="w+", dtype="<f8", shape=(len(texts), 3072))
            partial.chmod(0o600)
            try:
                for start in range(0, len(texts), 16):
                    batch = await cached_vectors(texts[start : start + 16], embedder, cache, identity)
                    values[start : start + len(batch)] = batch
                    values.flush()
                count_after = len(cache)
            finally:
                cache.close()
                del values
            array_path = directory / "r2-vectors.npy"
            partial.replace(array_path)
            frozen = {
                "schema": "fli.r2.vectors.v1",
                "sourceArtifactDigest": artifact["digest"],
                "embeddingIdentity": identity,
                "model": receipt["deployment"]["properties"]["model"],
                "arrayFile": array_path.name,
                "arraySha256": sha256(array_path.read_bytes()),
                "rows": {r["id"]: i for i, r in enumerate(rows)},
                "queries": {t["case"]: len(rows) + i for i, t in enumerate(artifact["tasks"])},
                "privateBinding": {"endpoint": endpoint, "deployment": deployment},
                "cacheEntriesBefore": before,
                "cacheEntriesAfter": count_after,
            }
            frozen["digest"] = digest(frozen)
            save(directory / "r2-vectors.json", frozen)
            status = "complete"
    finally:
        await credential.close()
        report = {
            "status": status,
            "embeddingIdentity": identity,
            "deploymentReceiptDigest": digest(receipt),
            "sourceArtifactDigest": artifact["digest"],
            "providerCounters": asdict(usage) if usage else None,
            "retryWaitMs": token_wait_ms,
            "elapsedMs": (time.perf_counter() - started) * 1000,
            "failedCallTokens": "unavailable",
            "heldoutExecuted": False,
        }
        name = f"r2-embedding-attempt-{time.time_ns()}.json"
        save(directory / name, report)
        print(json.dumps(report))


def exact_rank(rows: list[dict], vectors: dict, query: list[float], top: int = 8) -> list[dict]:
    """Independent exact cosine; stable row ID resolves numerical ties for repeatability."""
    import numpy as np

    matrix = np.asarray([to_float16_query(vectors[r["id"]]) for r in rows], dtype=np.float64)
    q = np.asarray(to_float16_query(query), dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1)
    qn = np.linalg.norm(q)
    if not np.isfinite(matrix).all() or not np.isfinite(q).all() or qn == 0 or (norms == 0).any():
        raise ValueError("nonfinite or zero vector")
    scores = matrix @ q / (norms * qn)
    selected = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i]["id"]))[:top]
    return [rows[i] for i in selected]


def selection_grade(measurements, row_counts):
    pairs = {}
    for m in measurements:
        if m["variant"] in pairs.setdefault(m["case"], {}):
            raise ValueError("duplicate comparison member")
        pairs[m["case"]][m["variant"]] = m
    if any(set(pair) != set(VARIANTS) for pair in pairs.values()):
        raise ValueError("missing comparison member")
    lost, gained = {}, {}
    for case, pair in pairs.items():
        baseline = set(pair[VARIANTS[0]]["deliveredRegionIds"])
        candidate = set(pair[VARIANTS[1]]["deliveredRegionIds"])
        if baseline - candidate:
            lost[case] = sorted(baseline - candidate)
        if candidate - baseline:
            gained[case] = sorted(candidate - baseline)
    limited = sorted({m["case"] for m in measurements if m["evidenceContextLimited"]})
    reduction = 1 - row_counts[VARIANTS[1]] / row_counts[VARIANTS[0]]
    return {
        "pairedTasks": len(pairs),
        "regressedTasks": sorted(lost),
        "improvedTasks": sorted(gained),
        "lostRegionIdsByCase": lost,
        "gainedRegionIdsByCase": gained,
        "evidenceContextLimitedCases": limited,
        "relativeVectorCountReduction": reduction,
        "candidatePassedPredeclaredGate": reduction >= 0.2 and not lost and not limited,
    }


def evaluate(args, directory):
    artifact = load_source(directory)
    vectors = json.loads((directory / "r2-vectors.json").read_text())
    expected = vectors.pop("digest")
    if digest(vectors) != expected or vectors["sourceArtifactDigest"] != artifact["digest"]:
        raise ValueError("frozen vectors or source binding changed")
    if not args.deployment_receipt:
        raise ValueError("evaluation requires a fresh post-embedding deployment receipt")
    receipt = json.loads(private_path(args.deployment_receipt).read_text())
    binding = vectors["privateBinding"]
    if deployment_identity(receipt, binding["endpoint"], binding["deployment"]) != vectors["embeddingIdentity"]:
        raise ValueError("embedding model/SKU changed during measured window")
    documents = {d["id"]: d for d in artifact["bundle"]["documents"]}
    tokenizer = TiktokenTokenizer()
    import numpy as np

    array_path = directory / vectors["arrayFile"]
    if array_path.name != "r2-vectors.npy" or sha256(array_path.read_bytes()) != vectors["arraySha256"]:
        raise ValueError("frozen vector bytes changed")
    array = np.load(array_path, mmap_mode="r", allow_pickle=False)
    matrices = {}
    for variant in VARIANTS:
        rows = artifact["variants"][variant]
        matrix = np.asarray([to_float16_query(array[vectors["rows"][r["id"]]]) for r in rows], dtype=np.float64)
        norms = np.linalg.norm(matrix, axis=1)
        if not np.isfinite(matrix).all() or (norms == 0).any():
            raise ValueError("invalid frozen passage vector")
        matrices[variant] = matrix / norms[:, None]
    measurements = []
    for task in artifact["tasks"]:
        for variant in VARIANTS:
            rows = artifact["variants"][variant]
            query = np.asarray(to_float16_query(array[vectors["queries"][task["case"]]]), dtype=np.float64)
            norm = np.linalg.norm(query)
            if not np.isfinite(query).all() or norm == 0:
                raise ValueError("invalid frozen query vector")
            scores = matrices[variant] @ (query / norm)
            selected = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i]["id"]))[:8]
            hits = [rows[i] for i in selected]
            measurement = source_relevance(task, documents, hits, tokenizer)
            measurements.append(
                measurement
                | {
                    "variant": variant,
                    "resultIds": [r["id"] for r in hits],
                    "evidenceOnlyTokenAllowance": 200000,
                    "evidenceContextLimited": measurement["expandedSourceJsonTokensEstimate"] > 200000,
                    "answeringContextFit": (
                        "unassessed; prompt/tool/output reserves are not measured by direct retrieval"
                    ),
                }
            )
    grade = selection_grade(measurements, {variant: len(rows) for variant, rows in artifact["variants"].items()})
    candidate_passed = grade["candidatePassedPredeclaredGate"]
    report = {
        "schema": "fli.r2.exact-source-relevance.v1",
        "status": "complete",
        "sourceArtifactDigest": artifact["digest"],
        "vectorsDigest": expected,
        "embeddingIdentity": vectors["embeddingIdentity"],
        "controls": artifact["controls"],
        "measurements": measurements,
        **grade,
        "recommendation": "coalesced-candidate-for-integration" if candidate_passed else "retain-granular-baseline",
        "allGoldTasks": {v: sum(m["allGoldDelivered"] for m in measurements if m["variant"] == v) for v in VARIANTS},
        "limitations": [
            "Exact vector source delivery, not Cosmos ANN or latency/RU evidence",
            "Whole original blocks expanded by the same rule; large tables may amplify delivery",
            "No agent answer or general semantic-accuracy claim; held-out remains reserved",
            "No production lifecycle or authorization change",
        ],
    }
    save(directory / "r2-evaluation.json", report)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "pairedTasks",
                    "regressedTasks",
                    "improvedTasks",
                    "relativeVectorCountReduction",
                    "recommendation",
                    "allGoldTasks",
                )
            }
        )
    )


def run(args):
    directory = private_path(args.work_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    with (directory / ".r2-lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "r2-local":
            if not args.source_dir:
                raise ValueError("r2-local requires --source-dir")
            local(args, directory)
        elif args.command == "r2-embed":
            if not args.deployment_receipt:
                raise ValueError("r2-embed requires --deployment-receipt")
            asyncio.run(embed(args, directory))
        elif args.command == "r2-evaluate":
            evaluate(args, directory)
