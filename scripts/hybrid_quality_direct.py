"""Frozen, read-only hybrid/vector source-recall experiment; raw evidence stays private.

Uses the unchanged API route and Azure stores in-process. The sole measurement
adapter is an immutable query embedder shared by both arms plus SDK diagnostics.
No provisioning, ingestion, deletes or production defaults are changed here.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from fli.api import create_app
from fli.config import Settings
from fli.embedding import AzureOpenAIEmbedder
from fli.ingestion import profile
from fli.presentation import compact_response
from fli.query import check_query, lexical_terms, partition_key_for, to_float16_query
from fli.structure_lab import digest
from scripts.retrieval_controls import deployment_binding, private_path, read
from scripts.retrieval_i1 import BINDING_KEYS
from scripts.score_retrieval_benchmark import normalize, validate_gold
from scripts.structure_lab import save

REPO = Path(__file__).resolve().parents[1]
ARMS = ("vector", "hybrid")
DIRECT = {
    "top": 8,
    "includeNeighbors": True,
    "repeats": 1,
    "order": "alternate-arm-first-by-case",
    "maxEvidenceTokens": 200000,
    "filters": {},
}
HEADERS = (
    "x-ms-request-charge",
    "x-ms-activity-id",
    "x-ms-documentdb-partitionkeyrangeid",
    "x-ms-documentdb-query-metrics",
    "x-ms-cosmos-index-utilization",
    "x-ms-retry-after-ms",
)
CAPTURE_FILES = (
    "controls.json",
    "config-private.json",
    "plan.json",
    "preflight-private.json",
    "settings-private.json",
    "query-cache-private.json",
    "source-before.json",
    "source-after.json",
    "captures-private.json",
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def question_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def code_hashes():
    paths = [Path(__file__), *sorted((REPO / "src").rglob("*.py")), *sorted((REPO / "cosmos").glob("*.json"))]
    return {str(p.relative_to(REPO)): sha(p) for p in paths}


def audit():
    policy = read(REPO / "cosmos/chunks.container.json")
    return {
        "clientTerms": "query.lower(); regex [0-9A-Za-z]+; first occurrence deduplication; first 16 terms",
        "clientStopwordRemoval": False,
        "clientStemming": False,
        "clientPhraseOrIdentifierPreservation": False,
        "maximumLexicalTerms": 16,
        "rrfWeights": "omitted; service defaults",
        "fullTextPolicy": policy["fullTextPolicy"],
        "vectorRouting": "library prefix partition_key=[libraryId]",
        "hybridRouting": "omit partition_key; retain exactly one libraryId equality",
        "fetchAtTop8": 16,
        "postQuerySelection": "active-generation visibility filter then first 8; neighbors independently expanded",
        "zeroLexicalTerms": "existing render_search falls back to vector; receipt records effective mode",
        "querySourceSha256": sha(REPO / "src/fli/query.py"),
        "searchSourceSha256": sha(REPO / "src/fli/search.py"),
        "containerDefinitionSha256": sha(REPO / "cosmos/chunks.container.json"),
    }


def validate_plan(plan, manifest_path, tasks_path):
    tasks, manifest = read(tasks_path), read(manifest_path)
    if plan.get("arms") != list(ARMS) or plan.get("direct") != DIRECT or plan.get("queryAudit") != audit():
        raise ValueError("direct comparison/query controls differ from frozen implementation")
    if plan.get("manifestSha256") != sha(manifest_path) or plan.get("tasksSha256") != sha(tasks_path):
        raise ValueError("corpus files differ from frozen plan")
    cases = [t["case"] for t in tasks]
    if not 40 <= len(cases) <= 60 or len(set(cases)) != len(cases):
        raise ValueError("expected 40–60 unique frozen confirmation questions")
    if any(
        t.get("split") != "confirmation" or not t.get("question") or type(t.get("answerable")) is not bool
        for t in tasks
    ):
        raise ValueError("each task needs a frozen confirmation question and answerability")
    return manifest, tasks


def settings_binding(settings):
    if settings.environment != "local" or settings.auth_mode != "dev":
        raise ValueError("measurement API requires authenticated local dev configuration")
    result = {k: str(getattr(settings, k.lower())) for k in sorted(BINDING_KEYS) if k != "SEARCH_MODE"}
    expected = {
        "SEARCH_DEFAULT_TOP": "8",
        "SEARCH_MAX_TOP": "20",
        "AOAI_EMBEDDING_DIMENSIONS": "3072",
        "AOAI_EMBEDDING_MODEL_REVISION": "1",
    }
    if any(result.get(k) != v for k, v in expected.items()):
        raise ValueError("query/embedding settings differ from retained foundation")
    return result


def model_binding(receipt, settings, *, fresh=True):
    return deployment_binding(
        receipt,
        configured_endpoint=settings.aoai_endpoint,
        configured_deployment=settings.aoai_embedding_deployment,
        model="text-embedding-3-large",
        revision="1",
        now=None if fresh else datetime.fromisoformat(receipt["capturedAt"]),
    )


def required_regions(task):
    return sorted({r["id"] for f in task.get("sourceFacts", []) for r in f.get("regions", [])})


def validate_fact_labels(tasks, gold):
    for task in tasks:
        regions, facts = required_regions(task), task.get("requiredFactIds", [])
        if bool(regions) != task["answerable"] or bool(facts) != task["answerable"]:
            raise ValueError("answerability must agree with required region and fact labels")
        if len(facts) != len(set(facts)):
            raise ValueError("duplicate required fact")
        for fact in facts:
            label = gold.get("facts", {}).get(fact, {})
            if not label.get("expectedFact") or not label.get("sourceRegionIds"):
                raise ValueError("required fact has no source-based gold definition")
            if not set(label["sourceRegionIds"]) <= set(regions):
                raise ValueError("required fact is not bound to required original source regions")


class FrozenEmbedder:
    model, version, dimensions = "text-embedding-3-large", "1", 3072

    def __init__(self, tasks, cache):
        self.by_text = {}
        if set(cache["queries"]) != {t["case"] for t in tasks}:
            raise ValueError("query cache must contain exactly the frozen tasks")
        for task in tasks:
            row = cache["queries"][task["case"]]
            vector = row["vector"]
            if row["questionSha256"] != question_sha(task["question"]) or len(vector) != self.dimensions:
                raise ValueError("query embedding does not bind frozen question/dimensions")
            to_float16_query(vector)
            if row.get("float16Sha256") != digest(to_float16_query(vector)):
                raise ValueError("cached float16 fingerprint changed")
            if task["question"] in self.by_text and self.by_text[task["question"]] != vector:
                raise ValueError("duplicate question has inconsistent cached embedding")
            self.by_text[task["question"]] = list(vector)

    async def embed(self, texts):
        if len(texts) != 1 or texts[0] not in self.by_text:
            raise ValueError("unfrozen query requested; no live embedding fallback")
        return [list(self.by_text[texts[0]])]


class RecordingContainer:
    def __init__(self, actual, library_id):
        self.actual, self.library_id = actual, library_id
        self.observations = []

    def __getattr__(self, name):
        return getattr(self.actual, name)

    def query_items(self, sql, **kwargs):
        check_query(sql)
        params = {p["name"]: p["value"] for p in kwargs["parameters"]}
        if params.get("@libraryId") != self.library_id:
            raise ValueError("diagnostic query escaped the owned library")
        ranked = "VectorDistance" in sql
        route = kwargs.get("partition_key")
        if ranked and route != partition_key_for(sql, self.library_id):
            raise ValueError("actual query route differs from validated routing")
        row = {"sql": sql, "parametersSha256": digest(kwargs["parameters"]), "partitionKey": route, "responses": []}
        self.observations.append(row)
        original = kwargs.get("raw_response_hook")

        def hook(response):
            headers = response.http_response.headers
            row["responses"].append(
                {
                    "status": getattr(response.http_response, "status_code", None),
                    "headers": {k: str(headers[k]) for k in HEADERS if k in headers},
                }
            )
            if original:
                original(response)

        kwargs.update(raw_response_hook=hook, populate_query_metrics=True, populate_index_metrics=True)
        return self.actual.query_items(sql, **kwargs)


def diagnostics_summary(observations):
    responses = [r for q in observations for r in q["responses"]]
    partitions = {
        r["headers"]["x-ms-documentdb-partitionkeyrangeid"]
        for r in responses
        if "x-ms-documentdb-partitionkeyrangeid" in r["headers"]
    }
    charge = [float(r["headers"]["x-ms-request-charge"]) for r in responses if "x-ms-request-charge" in r["headers"]]
    return {
        "observedChunkQueries": len(observations),
        "observedHttpResponses": len(responses),
        "visiblePartitionContactCount": len(partitions) if partitions else None,
        "visiblePartitionContactsComplete": False,
        "knownChunkResponseRequestCharge": sum(charge) if charge else None,
        "visibleThrottleResponses": sum(r["status"] == 429 for r in responses),
        "visibleServerErrorResponses": sum((r["status"] or 0) >= 500 for r in responses),
        "visibleRetryAfterResponses": sum("x-ms-retry-after-ms" in r["headers"] for r in responses),
        "totalRetries": None,
        "retryVisibilityComplete": False,
    }


def validate_delivery(row, task, snapshot, cache):
    if row["querySha256"] != question_sha(task["question"]):
        raise ValueError("capture question differs from frozen task")
    if row["queryVectorSha256"] != digest(to_float16_query(cache["queries"][task["case"]]["vector"])):
        raise ValueError("capture vector differs from shared query cache")
    raw, wire = row["rawResponse"], row["wire"]
    expected = compact_response(raw, DIRECT["maxEvidenceTokens"], view="compact", selectors="handles")
    if wire != expected or wire["metadata"]["budget"]["fits"] is not True:
        raise ValueError("actual delivered evidence differs from captured raw response or exceeds budget")
    documents = {d["id"]: d for d in snapshot["documents"]}
    if len(raw["results"]) > DIRECT["top"]:
        raise ValueError("capture exceeds frozen top")
    for hit in raw["results"]:
        doc = documents.get(hit["documentId"])
        if doc is None or hit.get("generation") != doc["activeGeneration"]:
            raise ValueError("capture contains foreign or invisible generation")
    ranked = [q for q in row["sdkObservations"] if "VectorDistance" in q["sql"]]
    effective = "hybrid" if row["arm"] == "hybrid" and lexical_terms(task["question"]) else "vector"
    if len(ranked) != 1 or ("ORDER BY RANK" in ranked[0]["sql"]) != (effective == "hybrid"):
        raise ValueError("actual ranking query does not match intended comparison arm")
    return effective


async def source_snapshot(client, library_id, agent_id, container):
    prefix = f"/v1/libraries/{library_id}"
    library = await client.get(prefix)
    documents = await client.get(prefix + "/documents")
    tools = await client.get(prefix + "/tools", params={"selectors": "handles"}, headers={"X-Agent-Id": agent_id})
    for r in (library, documents, tools):
        r.raise_for_status()
    sql = (
        "SELECT TOP 1000 c.id, c.documentId, c.generation, c.chunkIndex, c.contentHash, c._etag, "
        "c.embeddingModel, c.metadataVersion FROM c WHERE c.libraryId = @libraryId"
    )
    check_query(sql)
    chunks = [
        row
        async for row in container.query_items(
            sql, parameters=[{"name": "@libraryId", "value": library_id}], partition_key=[library_id]
        )
    ]
    if not chunks or len(chunks) >= 1000 or any(not c.get("_etag") or not c.get("contentHash") for c in chunks):
        raise ValueError("stored chunk snapshot empty, truncated or missing immutable identity")
    return {
        "library": library.json(),
        "documents": sorted(documents.json()["documents"], key=lambda d: d["id"]),
        "tools": tools.json()["tools"],
        "chunks": sorted(chunks, key=lambda c: (c["documentId"], c["id"])),
    }


def validate_snapshot(snapshot, manifest, config):
    library, docs = snapshot["library"], snapshot["documents"]
    recipe = library.get("ingestionProfile", {})
    expected = profile("microsoft-indexer-layout-v1")
    for key in (
        "id",
        "version",
        "provider",
        "extraction",
        "extractionOnly",
        "chunking",
        "enrichment",
        "workflowVersion",
    ):
        if recipe.get(key) != expected[key]:
            raise ValueError("source recipe differs from retained Microsoft profile")
    if recipe.get("embedding", {}).get("modelRevision") != "1":
        raise ValueError("source embedding revision not frozen")
    source_map = config["sourceMap"]
    sources = {d["id"]: d for d in manifest["documents"]}
    if set(source_map) != {d["id"] for d in docs} or sorted(source_map.values()) != sorted(sources):
        raise ValueError("evaluation scope must contain exactly the frozen originals")
    for doc in docs:
        source = sources[source_map[doc["id"]]]
        if (
            doc.get("status") != "ready"
            or doc.get("pendingGeneration")
            or doc.get("activeGeneration") != 1
            or doc["sha256"] != source["sha256"]
            or doc["fileName"] != source["fileName"]
        ):
            raise ValueError("source bytes/readiness/generation differ")
        job = doc.get("ingestionJob") or {}
        if job.get("profile") != recipe or job.get("generation") != 1 or job.get("sourceSha256") != source["sha256"]:
            raise ValueError("ingestion provenance differs from frozen source")
        for key, value in source.get("metadata", {}).items():
            if key in {"author", "periodStart", "periodEnd", "tags"} and doc.get(key) != value:
                raise ValueError("source metadata differs from manifest")
    chunks = snapshot.get("chunks", [])
    if not chunks or len({c["id"] for c in chunks}) != len(chunks):
        raise ValueError("stored chunk inventory empty or ambiguous")
    if set(c["documentId"] for c in chunks) != set(source_map):
        raise ValueError("stored chunks do not cover exactly the frozen documents")
    if any(c["generation"] != 1 or not c.get("contentHash") or not c.get("_etag") for c in chunks):
        raise ValueError("stored chunks contain pending/foreign generations or missing immutable identity")
    if any(c.get("embeddingModel") != "text-embedding-3-large" for c in chunks):
        raise ValueError("stored chunk embedding model differs from frozen foundation")


async def with_snapshot(config):
    settings = Settings.from_env()
    settings_binding(settings)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://measurement.local",
            headers={"Authorization": f"Bearer {settings.dev_api_key}"},
        ) as client,
    ):
        return await source_snapshot(client, config["libraryId"], config["agentId"], app.state.searcher.chunks._c)


async def prepare(config_path, plan_path, manifest_path, tasks_path, output):
    """Bounded prospective ranking sanity check; never an index-readiness or recall oracle."""
    root = private_path(output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    config, plan = read(config_path), read(plan_path)
    manifest, tasks = validate_plan(plan, manifest_path, tasks_path)
    probes = plan.get("preparationQueries", [])
    if not 2 <= len(probes) <= 6 or len({p["id"] for p in probes}) != len(probes):
        raise ValueError("freeze two to six distinct preparation queries before probing")
    if any(p["question"] in {t["question"] for t in tasks} for p in probes):
        raise ValueError("confirmation questions cannot be readiness probes")
    sources = {s["id"] for s in manifest["documents"]}
    if any(not p.get("expectedSourceIds") or not set(p["expectedSourceIds"]) <= sources for p in probes):
        raise ValueError("preparation expected-source labels must name frozen sources")
    settings = Settings.from_env()
    binding = settings_binding(settings)
    result = {
        "status": "partial",
        "planSha256": sha(plan_path),
        "settingsBindingSha256": digest(binding),
        "codeSha256": code_hashes(),
        "queries": [],
        "embeddingUsage": {},
        "startedAt": datetime.now(UTC).isoformat(),
    }
    save(root / "config-private.json", config)
    app = create_app(settings)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://measurement.local",
                headers={"Authorization": f"Bearer {settings.dev_api_key}", "X-Agent-Id": config["agentId"]},
                timeout=180,
            ) as client,
        ):
            searcher = app.state.searcher
            before = await source_snapshot(client, config["libraryId"], config["agentId"], searcher.chunks._c)
            validate_snapshot(before, manifest, config)
            save(root / "source-before.json", before)
            prep_tasks = [{"case": p["id"], "question": p["question"]} for p in probes]
            cache = {"queries": {}}
            embedder = searcher.embedder
            for probe in prep_tasks:
                try:
                    [vector] = await embedder.embed([probe["question"]])
                finally:
                    result["embeddingUsage"] = asdict(embedder.usage)
                    save(root / "preparation-private.json", result)
                cache["queries"][probe["case"]] = {
                    "questionSha256": question_sha(probe["question"]),
                    "vector": vector,
                    "float16Sha256": digest(to_float16_query(vector)),
                }
                save(root / "query-cache-private.json", cache)
            searcher.embedder = FrozenEmbedder(prep_tasks, cache)
            recorder = RecordingContainer(searcher.chunks._c, config["libraryId"])
            searcher.chunks._c = recorder
            original, raw = searcher.search, {}

            async def observed(*args, **kwargs):
                response = await original(*args, **kwargs)
                raw["response"] = copy.deepcopy(response)
                return response

            searcher.search = observed
            for number, task in enumerate(prep_tasks):
                for arm in ARMS if number % 2 == 0 else tuple(reversed(ARMS)):
                    searcher.mode, recorder.observations = arm, []
                    raw.clear()
                    row = {
                        "case": task["case"],
                        "arm": arm,
                        "status": "failed",
                        "querySha256": question_sha(task["question"]),
                        "queryVectorSha256": cache["queries"][task["case"]]["float16Sha256"],
                    }
                    try:
                        response = await client.post(
                            f"/v1/libraries/{config['libraryId']}/search",
                            params={"view": "compact", "selectors": "handles", "maxEvidenceTokens": 200000},
                            json={"query": task["question"], "top": 8, "includeNeighbors": True, "filters": {}},
                        )
                        row.update(
                            httpStatus=response.status_code,
                            wire=response.json(),
                            rawResponse=raw.get("response"),
                            sdkObservations=recorder.observations,
                        )
                        response.raise_for_status()
                        row["effectiveMode"] = validate_delivery(row, task, before, cache)
                        row["status"] = "complete"
                    finally:
                        row["sdkObservations"] = recorder.observations
                        result["queries"].append(row)
                        save(root / "preparation-private.json", result)
            after = await source_snapshot(client, config["libraryId"], config["agentId"], searcher.chunks._c)
            save(root / "source-after.json", after)
            if before != after or result["codeSha256"] != code_hashes():
                raise ValueError("source rows or measurement code changed during preparation")
            hybrid = [r for r in result["queries"] if r["arm"] == "hybrid"]
            ranks = {tuple(hit["chunkId"] for hit in row["rawResponse"]["results"]) for row in hybrid}
            expected_present = all(
                set(probe["expectedSourceIds"])
                <= {config["sourceMap"][hit["documentId"]] for hit in row["rawResponse"]["results"]}
                for probe, row in zip(probes, hybrid, strict=True)
            )
            counts = Counter(c["documentId"] for c in before["chunks"])
            result.update(
                status="complete",
                finishedAt=datetime.now(UTC).isoformat(),
                sourceSnapshotSha256=digest(before),
                storedRowsSha256=digest(before["chunks"]),
                actualCorpusReadiness={
                    "documents": len(before["documents"]),
                    "chunks": len(before["chunks"]),
                    "documentsWithMultipleChunks": sum(n > 1 for n in counts.values()),
                    "prospectiveCorpusAdequacyPassed": len(before["chunks"]) > 16
                    and sum(n > 1 for n in counts.values()) >= 6,
                    "indexBuildProgress": None,
                    "indexExecutionMode": "unknown",
                    "steadyStateIndexReadinessClaimed": False,
                },
                hybridRankingSanity={
                    "distinctQueryRankLists": len(ranks),
                    "expectedSourcesPresent": expected_present,
                    "passed": len(ranks) > 1 and expected_present,
                    "meaning": "different ranked outputs on frozen preparation questions; no ANN/exact-rank oracle",
                },
            )
    finally:
        save(root / "preparation-private.json", result)
    return {k: result[k] for k in ("status", "actualCorpusReadiness", "hybridRankingSanity", "sourceSnapshotSha256")}


async def embed(tasks_path, deployment_path, output):
    from azure.identity.aio import DefaultAzureCredential

    from fli.docintel import SCOPE
    from fli.tokens import CachedToken

    root = private_path(output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    settings = Settings.from_env()
    settings_binding(settings)
    before = read(deployment_path)
    binding = model_binding(before, settings)
    tasks = read(tasks_path)
    result = {"status": "partial", "tasksSha256": sha(tasks_path), "deploymentBinding": binding, "queries": {}}
    save(root / "deployment-before.json", before)
    save(root / "settings-private.json", settings_binding(settings))
    try:
        async with DefaultAzureCredential() as credential, httpx.AsyncClient() as http:
            embedder = AzureOpenAIEmbedder(
                settings.aoai_endpoint,
                settings.aoai_embedding_deployment,
                CachedToken(credential, SCOPE),
                http,
                dimensions=3072,
            )
            for task in tasks:
                started = time.perf_counter()
                previous = asdict(embedder.usage)
                try:
                    [vector] = await embedder.embed([task["question"]])
                finally:
                    result["lastAttemptUsage"] = asdict(embedder.usage)
                    save(root / "cache.json", result)
                usage = asdict(embedder.usage)
                result["queries"][task["case"]] = {
                    "questionSha256": question_sha(task["question"]),
                    "vector": vector,
                    "float16Sha256": digest(to_float16_query(vector)),
                    "usage": {k: usage[k] - previous[k] for k in ("requests", "prompt_tokens", "throttled", "retries")},
                    "tokensReported": usage["tokens_reported"],
                    "totalMs": (time.perf_counter() - started) * 1000,
                }
                save(root / "cache.json", result)
        result["status"] = "pending_deployment_recheck"
        result["completedAt"] = datetime.now(UTC).isoformat()
        save(root / "cache.json", result)
    except BaseException:
        save(root / "cache.json", result)
        raise
    return {"status": result["status"], "queries": len(result["queries"])}


def finalize_embeddings(root, after_path):
    root = private_path(root)
    cache = read(root / "cache.json")
    settings = Settings.from_env()
    after = read(after_path)
    if cache["status"] != "pending_deployment_recheck" or model_binding(after, settings) != cache["deploymentBinding"]:
        raise ValueError("embedding run incomplete or deployment changed")
    if datetime.fromisoformat(after["capturedAt"]) < datetime.fromisoformat(cache["completedAt"]):
        raise ValueError("post-deployment capture predates embedding completion")
    save(root / "deployment-after.json", after)
    cache.update(
        status="complete",
        deploymentReceiptsSha256={n: sha(root / n) for n in ("deployment-before.json", "deployment-after.json")},
    )
    save(root / "cache.json", cache)
    return {"status": "complete", "queries": len(cache["queries"])}


async def capture(config_path, plan_path, manifest_path, tasks_path, cache_dir, preflight_path, output):
    root = private_path(output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    config, plan, preflight = read(config_path), read(plan_path), read(preflight_path)
    manifest, tasks = validate_plan(plan, manifest_path, tasks_path)
    settings = Settings.from_env()
    binding = settings_binding(settings)
    cache_root = private_path(cache_dir)
    cache = read(cache_root / "cache.json")
    if cache["status"] != "complete" or cache["tasksSha256"] != sha(tasks_path):
        raise ValueError("query cache incomplete or different questions")
    for name, value in cache["deploymentReceiptsSha256"].items():
        if sha(cache_root / name) != value:
            raise ValueError("embedding management receipt changed")
        if model_binding(read(cache_root / name), settings, fresh=False) != cache["deploymentBinding"]:
            raise ValueError("query embedding deployment does not bind runtime")
    if (
        preflight.get("planSha256") != sha(plan_path)
        or preflight.get("settingsBindingSha256") != digest(binding)
        or preflight.get("ownedLibraryId") != config["libraryId"]
        or not isinstance(preflight.get("actualCorpusReadiness"), dict)
        or preflight["actualCorpusReadiness"].get("prospectiveCorpusAdequacyPassed") is not True
        or any(
            preflight.get(k) is not True
            for k in (
                "preparationAccepted",
                "inheritedCanaryPassed",
                "incarnationAndPoliciesMatch",
                "hybridRanksMeaningfully",
            )
        )
    ):
        raise ValueError("coordinator preparation/canary/ranking acceptance missing or unbound")
    ledger = private_path(config["executionLedger"])
    if ledger.is_relative_to(root):
        raise ValueError("execution ledger must survive outside output directory")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("x") as f:
        json.dump(
            {"planSha256": sha(plan_path), "outputDirectory": str(root), "claimedAt": datetime.now(UTC).isoformat()}, f
        )
    frozen = FrozenEmbedder(tasks, cache)
    controls = {
        "planSha256": sha(plan_path),
        "manifestSha256": sha(manifest_path),
        "tasksSha256": sha(tasks_path),
        "codeSha256": code_hashes(),
        "settingsBindingSha256": digest(binding),
        "queryCacheSha256": sha(cache_root / "cache.json"),
        "preflightSha256": sha(preflight_path),
        "configSha256": sha(config_path),
    }
    for name, value in (
        ("controls.json", controls),
        ("config-private.json", config),
        ("plan.json", plan),
        ("preflight-private.json", preflight),
        ("settings-private.json", binding),
        ("query-cache-private.json", cache),
    ):
        save(root / name, value)
    for name, path in (
        ("config-private.json", config_path),
        ("plan.json", plan_path),
        ("preflight-private.json", preflight_path),
        ("query-cache-private.json", cache_root / "cache.json"),
    ):
        (root / name).write_bytes(Path(path).read_bytes())
    rows = []
    app = create_app(settings)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://measurement.local",
                headers={"Authorization": f"Bearer {settings.dev_api_key}", "X-Agent-Id": config["agentId"]},
                timeout=180,
            ) as client,
        ):
            before = await source_snapshot(client, config["libraryId"], config["agentId"], app.state.searcher.chunks._c)
            validate_snapshot(before, manifest, config)
            actual_counts = Counter(c["documentId"] for c in before["chunks"])
            readiness = preflight["actualCorpusReadiness"]
            if (
                readiness.get("documents") != len(before["documents"])
                or readiness.get("chunks") != len(before["chunks"])
                or readiness.get("documentsWithMultipleChunks") != sum(n > 1 for n in actual_counts.values())
                or len(before["chunks"]) <= 16
                or sum(n > 1 for n in actual_counts.values()) < 6
            ):
                raise ValueError("actual corpus does not meet frozen prospective adequacy gate")
            if digest(before) != preflight["sourceSnapshotSha256"]:
                raise ValueError("live source snapshot differs from accepted preflight")
            save(root / "source-before.json", before)
            searcher = app.state.searcher
            searcher.embedder = frozen
            recorder = RecordingContainer(searcher.chunks._c, config["libraryId"])
            searcher.chunks._c = recorder
            original = searcher.search
            raw = {}

            async def observed(*args, **kwargs):
                response = await original(*args, **kwargs)
                raw["response"] = copy.deepcopy(response)
                return response

            searcher.search = observed
            for number, task in enumerate(tasks):
                order = ARMS if number % 2 == 0 else tuple(reversed(ARMS))
                for arm in order:
                    searcher.mode = arm
                    recorder.observations = []
                    raw.clear()
                    started = time.perf_counter()
                    row = {
                        "case": task["case"],
                        "arm": arm,
                        "querySha256": question_sha(task["question"]),
                        "queryVectorSha256": digest(to_float16_query(cache["queries"][task["case"]]["vector"])),
                        "lexicalTerms": lexical_terms(task["question"]) if arm == "hybrid" else [],
                        "status": "failed",
                    }
                    try:
                        response = await client.post(
                            f"/v1/libraries/{config['libraryId']}/search",
                            params={"view": "compact", "selectors": "handles", "maxEvidenceTokens": 200000},
                            json={"query": task["question"], "top": 8, "includeNeighbors": True, "filters": {}},
                        )
                        row.update(
                            httpStatus=response.status_code, wire=response.json(), rawResponse=raw.get("response")
                        )
                        response.raise_for_status()
                        if not raw.get("response") or response.json().get("contract") != "fli.compact.v1":
                            raise ValueError("actual API source response missing")
                        row["sdkObservations"] = recorder.observations
                        row["effectiveMode"] = validate_delivery(row, task, before, cache)
                        row["status"] = "complete"
                    finally:
                        row.update(
                            totalMs=(time.perf_counter() - started) * 1000, sdkObservations=recorder.observations
                        )
                        rows.append(row)
                        save(root / "captures-private.json", rows)
            after = await source_snapshot(client, config["libraryId"], config["agentId"], app.state.searcher.chunks._c)
            save(root / "source-after.json", after)
            if before != after or code_hashes() != controls["codeSha256"]:
                raise ValueError("sources or code changed during direct comparison")
        result = {
            "status": "captured",
            "finishedAt": datetime.now(UTC).isoformat(),
            "completedSearches": len(rows),
            "sourceSnapshotSha256": digest(before),
            **controls,
        }
        result["artifactsSha256"] = {name: sha(root / name) for name in CAPTURE_FILES}
        save(root / "capture-receipt.json", result)
    except BaseException:
        save(
            root / "capture-receipt.json",
            {"status": "partial", "completedSearches": sum(r["status"] == "complete" for r in rows), **controls},
        )
        raise
    return {"status": "captured", "completedSearches": len(rows)}


def bodies(row, source_map, expanded):
    result = defaultdict(list)
    for hit in row["rawResponse"]["results"]:
        if hit["documentId"] not in source_map:
            raise ValueError("retrieval contains foreign document")
        source = source_map[hit["documentId"]]
        result[source].append(normalize(hit.get("text", "")))
        if expanded:
            result[source].extend(normalize(n.get("text", "")) for n in hit.get("neighbors") or [])
    return result


def diagnostics(task, row, gold, source_map):
    out = {}
    for name, expanded in (("initial", False), ("expanded", True)):
        delivered = bodies(row, source_map, expanded)
        matched = []
        for region in required_regions(task):
            expected = gold["regions"][region]
            text = normalize(expected["expectedText"])
            if text and any(text in b for b in delivered[expected["sourceId"]]):
                matched.append(region)
        out[name + "TextMatchRegionIds"] = matched
    return out


def score(root, manifest_path, tasks_path, gold_path, reviews_path, output):
    root, output = private_path(root), private_path(output)
    controls, plan = read(root / "controls.json"), read(root / "plan.json")
    manifest, tasks = validate_plan(plan, manifest_path, tasks_path)
    gold, reviews = read(gold_path), read(reviews_path)
    validate_gold(manifest, tasks, gold)
    validate_fact_labels(tasks, gold)
    if sha(gold_path) != plan["goldSha256"] or controls["codeSha256"] != code_hashes():
        raise ValueError("frozen source labels or measurement code changed")
    capture_receipt = read(root / "capture-receipt.json")
    if capture_receipt.get("status") != "captured" or set(capture_receipt.get("artifactsSha256", {})) != set(
        CAPTURE_FILES
    ):
        raise ValueError("capture has no complete artifact binding")
    for name, value in capture_receipt["artifactsSha256"].items():
        if sha(root / name) != value:
            raise ValueError("capture artifact changed after execution")
    if any(capture_receipt.get(k) != v for k, v in controls.items()):
        raise ValueError("capture controls changed")
    for key, name in (
        ("planSha256", "plan.json"),
        ("configSha256", "config-private.json"),
        ("preflightSha256", "preflight-private.json"),
        ("queryCacheSha256", "query-cache-private.json"),
    ):
        if controls[key] != sha(root / name):
            raise ValueError("capture control binding changed")
    before, after = read(root / "source-before.json"), read(root / "source-after.json")
    config = read(root / "config-private.json")
    cache = read(root / "query-cache-private.json")
    FrozenEmbedder(tasks, cache)
    validate_snapshot(before, manifest, config)
    if (
        capture_receipt.get("status") != "captured"
        or before != after
        or digest(before) != capture_receipt["sourceSnapshotSha256"]
    ):
        raise ValueError("direct capture incomplete or source snapshots changed")
    captures = read(root / "captures-private.json")
    expected = {(t["case"], arm) for t in tasks for arm in ARMS}
    if (
        len(captures) != len(expected)
        or {(r["case"], r["arm"]) for r in captures} != expected
        or any(r["status"] != "complete" for r in captures)
    ):
        raise ValueError("every frozen direct query/arm requires one complete capture")
    judged = {(r["case"], r["arm"]): r for r in reviews}
    if len(reviews) != len(expected) or set(judged) != expected:
        raise ValueError("every frozen direct query/arm requires explicit review")
    by_case = {t["case"]: t for t in tasks}
    rows = []
    for row in captures:
        task, review = by_case[row["case"]], judged[row["case"], row["arm"]]
        validate_delivery(row, task, before, cache)
        regions, facts = set(required_regions(task)), set(task.get("requiredFactIds", []))
        hits = row["rawResponse"]["results"]
        value = {
            "case": row["case"],
            "arm": row["arm"],
            "families": task["families"],
            "answerable": task["answerable"],
            "requiredRegionCount": len(regions),
            "requiredFactCount": len(facts),
        }
        for prefix in ("initial", "expanded"):
            for suffix, wanted in (("Regions", regions), ("Facts", facts)):
                got = review.get(prefix + suffix)
                if not isinstance(got, list) or len(got) != len(set(got)) or not set(got) <= wanted:
                    raise ValueError("review must give unique original required region/fact IDs")
                value[prefix + suffix] = sorted(got)
            if type(review.get(prefix + "EvidenceReviewed")) is not bool or not review[prefix + "EvidenceReviewed"]:
                raise ValueError("source-coordinate and fact evidence review is required")
            value[prefix + "RegionRecall"] = len(value[prefix + "Regions"]) / len(regions) if regions else None
            value[prefix + "FactRecall"] = len(value[prefix + "Facts"]) / len(facts) if facts else None
            value[prefix + "AllRequiredEvidence"] = (
                (set(value[prefix + "Regions"]) == regions and set(value[prefix + "Facts"]) == facts)
                if regions or facts
                else None
            )
        if not set(value["initialRegions"]) <= set(value["expandedRegions"]) or not set(value["initialFacts"]) <= set(
            value["expandedFacts"]
        ):
            raise ValueError("neighbor expansion cannot remove delivered source evidence")
        rank_groups = {}
        for label in ("Useful", "Irrelevant", "Unjudged"):
            ranks = review.get("initial" + label + "HitRanks")
            if not isinstance(ranks, list) or len(ranks) != len(set(ranks)) or any(type(i) is not int for i in ranks):
                raise ValueError("hit precision requires explicit useful, irrelevant and unjudged ranks")
            rank_groups[label] = set(ranks)
        ranks = [rank for group in rank_groups.values() for rank in group]
        if len(ranks) != len(set(ranks)) or set(ranks) != set(range(1, len(hits) + 1)):
            raise ValueError("every initial hit must be useful, irrelevant or unjudged exactly once")
        useful = rank_groups["Useful"]
        if review.get("unanswerableHelpfulContext") not in {None, "helpful_absence_context", "not_helpful", "unjudged"}:
            raise ValueError("absence context must be an allowlisted judgment, never source text")
        labeled = sum(
            bool(
                diagnostics(task, {"rawResponse": {"results": [hit]}}, gold, config["sourceMap"])[
                    "initialTextMatchRegionIds"
                ]
            )
            for hit in hits
        )
        value.update(
            initialUsefulHitCount=len(useful),
            initialHitCount=len(hits),
            initialIrrelevantHitCount=len(rank_groups["Irrelevant"]),
            initialUnjudgedHitCount=len(rank_groups["Unjudged"]),
            initialUsefulHitPrecision=(
                len(useful) / len(hits) if hits and task["answerable"] and not rank_groups["Unjudged"] else None
            ),
            labeledEvidenceHitPrecision=labeled / len(hits) if hits and task["answerable"] else None,
            unanswerableHelpfulContext=review.get("unanswerableHelpfulContext") if not task["answerable"] else None,
            diagnostics=diagnostics(task, row, gold, config["sourceMap"]),
            knownApiRequestCharge=(row["rawResponse"].get("usage") or {}).get("requestCharge"),
            totalRequestCharge=None,
            requestChargeComplete=False,
            totalMs=row["totalMs"],
            sdkDiagnostics=diagnostics_summary(row["sdkObservations"]),
            providerEmbeddingCallsDuringSearch=0,
            queryEmbeddingPreparationAccountedSeparately=True,
        )
        rows.append(value)
    paired = []
    for task in tasks:
        members = {r["arm"]: r for r in rows if r["case"] == task["case"]}
        p = {"case": task["case"], "families": task["families"], "answerable": task["answerable"]}
        for prefix in ("initial", "expanded"):
            a, b = members["vector"][prefix + "RegionRecall"], members["hybrid"][prefix + "RegionRecall"]
            p[prefix + "RegionOutcome"] = (
                None if a is None or b is None else "hybrid_win" if b > a else "vector_win" if a > b else "tie"
            )
            p[prefix + "HybridGainedRegions"] = sorted(
                set(members["hybrid"][prefix + "Regions"]) - set(members["vector"][prefix + "Regions"])
            )
            p[prefix + "HybridLostRegions"] = sorted(
                set(members["vector"][prefix + "Regions"]) - set(members["hybrid"][prefix + "Regions"])
            )
            a, b = members["vector"][prefix + "FactRecall"], members["hybrid"][prefix + "FactRecall"]
            p[prefix + "FactOutcome"] = (
                None if a is None or b is None else "hybrid_win" if b > a else "vector_win" if a > b else "tie"
            )
            p[prefix + "HybridGainedFacts"] = sorted(
                set(members["hybrid"][prefix + "Facts"]) - set(members["vector"][prefix + "Facts"])
            )
            p[prefix + "HybridLostFacts"] = sorted(
                set(members["vector"][prefix + "Facts"]) - set(members["hybrid"][prefix + "Facts"])
            )
        paired.append(p)
    family = {}
    for name in sorted({f for t in tasks for f in t["families"]}):
        group = [p for p in paired if name in p["families"]]
        family[name] = {
            "questions": len(group),
            **{
                prefix: dict(Counter(p[prefix + "RegionOutcome"] or "not_applicable" for p in group))
                for prefix in ("initial", "expanded")
            },
        }
    artifact_paths = {
        n: str(root / n)
        for n in (
            "controls.json",
            "config-private.json",
            "plan.json",
            "preflight-private.json",
            "settings-private.json",
            "query-cache-private.json",
            "source-before.json",
            "source-after.json",
            "captures-private.json",
            "capture-receipt.json",
        )
    }
    artifact_paths["reviews-private.json"] = str(private_path(reviews_path))
    result = {
        "schema": "fli.hybrid-quality.direct.v1",
        "status": "complete",
        "finishedAt": capture_receipt["finishedAt"],
        "reviewCompletedAt": datetime.now(UTC).isoformat(),
        **controls,
        "sourceSnapshotSha256": digest(before),
        "rows": rows,
        "paired": paired,
        "families": family,
        "summary": {
            arm: {
                "answerableQuestions": sum(r["answerable"] for r in rows if r["arm"] == arm),
                **{
                    prefix + metric: sum(values) / len(values) if values else None
                    for prefix in ("initial", "expanded")
                    for metric in ("RegionRecall", "FactRecall", "AllRequiredEvidence")
                    for values in [
                        [r[prefix + metric] for r in rows if r["arm"] == arm and r[prefix + metric] is not None]
                    ]
                },
            }
            for arm in ARMS
        },
        "rawArtifactPaths": artifact_paths,
        "rawArtifactsSha256": {n: sha(p) for n, p in artifact_paths.items()},
        "limitations": [
            "Source/fact coverage and usefulness require original-source assistant review; "
            "text matches are diagnostics only.",
            "No independent exact hybrid or ANN recall claim.",
            "Known API RU excludes unobservable SDK/query-plan charges; total unavailable.",
            "Cached query embeddings exclude embedding latency from paired search timing; "
            "preparation cost recorded separately.",
            "One paired direct pass; task families overlap and repeat answers are not independent questions.",
        ],
    }
    save(output, result)
    return {"status": "complete", "searches": len(rows), "families": family}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("audit", "snapshot", "prepare", "embed", "finalize-embeddings", "capture", "score")
    )
    for key in (
        "config",
        "plan",
        "manifest",
        "tasks",
        "cache-dir",
        "preflight",
        "deployment",
        "source",
        "gold",
        "reviews",
        "output",
    ):
        parser.add_argument("--" + key, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    required = {
        "audit": (),
        "snapshot": ("config", "output"),
        "prepare": ("config", "plan", "manifest", "tasks", "output"),
        "embed": ("tasks", "deployment", "output"),
        "finalize-embeddings": ("source", "deployment"),
        "capture": ("config", "plan", "manifest", "tasks", "cache_dir", "preflight", "output"),
        "score": ("source", "manifest", "tasks", "gold", "reviews", "output"),
    }
    for key in required[args.operation]:
        if getattr(args, key) is None:
            parser.error("--" + key.replace("_", "-") + " is required for " + args.operation)
    if args.operation == "audit":
        result = audit()
    elif args.operation == "snapshot":
        result = asyncio.run(with_snapshot(read(args.config)))
        save(private_path(args.output), result)
        result = {"sourceSnapshotSha256": digest(result)}
    elif args.operation == "embed":
        result = asyncio.run(embed(args.tasks, args.deployment, args.output))
    elif args.operation == "prepare":
        result = asyncio.run(prepare(args.config, args.plan, args.manifest, args.tasks, args.output))
    elif args.operation == "finalize-embeddings":
        result = finalize_embeddings(args.source, args.deployment)
    elif args.operation == "capture":
        result = asyncio.run(
            capture(args.config, args.plan, args.manifest, args.tasks, args.cache_dir, args.preflight, args.output)
        )
    else:
        result = score(args.source, args.manifest, args.tasks, args.gold, args.reviews, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
