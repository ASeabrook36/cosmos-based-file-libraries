"""Versioned, coordinator-run, read-only Python/.NET query comparison.

Only explicit `run` uses Cosmos. Inputs, credentials and raw wire/source material
must be private; `prepare`, `summarize` and tests are local. Never load or delete a
library. The explicit-prefix hybrid cell is an authorized diagnostic exception to
the application's unchanged E27 routing rule, not a default or API modification.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import AioHttpTransport
from azure.cosmos.aio import CosmosClient

from fli.query import SearchFilters, check_query, lexical_terms, render_search, to_float16_query
from fli.structure_lab import digest
from scripts.canary import definition_mismatches, target_fingerprint
from scripts.hybrid_quality_direct import FrozenEmbedder, validate_snapshot
from scripts.retrieval_controls import private_path

REPO = Path(__file__).resolve().parents[1]
BASE = "8f7d436987724abac1efe633d071db66f8cbf4ea"
SCHEMA = "fli.hybrid-sdk-comparison.v1"
CELLS = tuple(
    (sdk, route) for sdk in ("python", "dotnet") for route in ("vector-prefix", "hybrid-prefix", "hybrid-where")
)
TOP = 16
REPEATS = 2
REQUEST_BUDGET = 12000
DEADLINE_SECONDS = 1800
SCORE_EPSILON = 1e-6
INVENTORY_SQL = (
    "SELECT TOP 1000 c.id, c.documentId, c.generation, c.chunkIndex, c.contentHash, c._etag, "
    "c.embeddingModel, c.metadataVersion FROM c WHERE c.libraryId = @libraryId"
)
HEADERS = {
    "x-ms-request-charge",
    "x-ms-activity-id",
    "x-ms-documentdb-partitionkeyrangeid",
    "x-ms-documentdb-partitionkey",
    "x-ms-documentdb-query-metrics",
    "x-ms-cosmos-index-utilization",
    "x-ms-substatus",
    "x-ms-retry-after-ms",
    "retry-after",
    "x-ms-request-duration-ms",
    "x-ms-item-count",
    "x-ms-continuation",
    "a-im",
    "if-none-match",
    "etag",
    "x-ms-version",
    "x-ms-supported-query-features",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = private_path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def reference(path):
    path = private_path(path)
    require(path.is_file(), "private reference missing")
    return {"path": str(path), "sha256": sha(path)}


def checked(ref):
    path = private_path(ref["path"])
    require(path.is_file() and not path.is_symlink() and sha(path) == ref["sha256"], "input binding differs")
    return path


def code_hashes():
    paths = [
        Path(__file__),
        REPO / "src/fli/query.py",
        REPO / "scripts/hybrid_quality_direct.py",
        REPO / "scripts/canary.py",
        REPO / "scripts/retrieval_controls.py",
        *sorted((REPO / "cosmos").glob("*.json")),
        *[
            REPO / "tools/cosmos-sdk-probe" / name
            for name in ("Program.cs", "CosmosSdkProbe.csproj", "packages.lock.json", "global.json")
        ],
    ]
    return {str(p.relative_to(REPO)): sha(p) for p in paths}


def verify_benchmark(controls):
    require(
        controls["tasksSha256"] == sha(REPO / "docs/testing/hybrid-quality/tasks.json")
        and controls["manifestSha256"] == sha(REPO / "docs/testing/hybrid-quality/manifest.json"),
        "consumed benchmark files changed",
    )


def selected_headers(headers):
    return {
        str(k).lower(): str(v)
        for k, v in headers.items()
        if str(k).lower() in HEADERS or any(s in str(k).lower() for s in ("query", "partition", "epk", "retry"))
    }


def schedule(cases):
    result = []
    for repeat in range(1, REPEATS + 1):
        for index, case in enumerate(cases):
            cells = CELLS[index % len(CELLS) :] + CELLS[: index % len(CELLS)]
            if repeat == 2:
                cells = tuple(reversed(cells))
            for sdk, route in cells:
                result.append(
                    {
                        "case": case,
                        "repeat": repeat,
                        "sdk": sdk,
                        "route": route,
                        "id": f"r{repeat}-{case}-{sdk}-{route}",
                        "phase": "measured",
                    }
                )
    return result


def make_command(case, route, library, query, *, identifier, document=None, reference_query=False):
    vector = to_float16_query(query["vector"])
    terms = query["terms"] if route.startswith("hybrid") else []
    rendered = render_search(
        library, vector, terms, SearchFilters(), TOP, document_id=document, generation=1 if document else None
    )
    check_query(rendered.sql)
    sql = rendered.sql
    if reference_query:
        require(route == "vector-prefix" and document, "exact reference requires full document key")
        exact = "VectorDistance(c.embedding, @queryVector, true, {'dataType': 'float16'})"
        sql = sql.replace("VectorDistance(c.embedding, @queryVector, false, {'dataType': 'float16'})", exact)
        sql = sql.replace("SELECT TOP 16 ", "SELECT TOP 16 " + exact + " AS score, ", 1)
    return {
        "id": identifier,
        "case": case,
        "sql": sql,
        "parameters": rendered.parameters,
        "partitionKey": None if route == "hybrid-where" else [library, document] if document else [library],
        "maxItemCount": TOP,
    }


def validate_scope(config, snapshot, owner, protected):
    lib = config["libraryId"]
    require(
        owner.get("createdByThisRun") is True
        and owner.get("readyCompleted") is True
        and not owner.get("deleteAttempted")
        and owner["library"]["id"] == lib,
        "owned ready scope required",
    )
    require(protected and lib not in protected and len(protected) == len(set(protected)), "protected scope collision")
    require(sorted(protected) == owner["contract"]["protectedLibraryIds"], "protected inventory differs")
    require(
        snapshot["library"]["id"] == lib and snapshot["library"].get("createdBy") == owner["runId"],
        "library owner differs",
    )
    require(snapshot["library"].get("status") == "active", "library is not active")
    require(len(snapshot["documents"]) == 8 and len(snapshot["chunks"]) >= TOP, "expected owned eight-source pilot")
    counts = Counter(c["documentId"] for c in snapshot["chunks"])
    require(
        all(counts[d["id"]] == d["chunkCount"] <= TOP for d in snapshot["documents"]),
        "exact reference TOP16 must cover every document; revise bounds before running",
    )
    source_map = {v["documentId"]: key for key, v in owner["sources"].items()}
    validate_snapshot(snapshot, read(REPO / "docs/testing/hybrid-quality/manifest.json"), {"sourceMap": source_map})
    require(
        all(
            d["generation"] == d["activeGeneration"] == 1 and not d.get("pendingGeneration")
            for d in snapshot["documents"]
        ),
        "generation changed",
    )
    return source_map


def validate_cache(path, tasks):
    cache = read(path)
    expected_model = {"format": "OpenAI", "name": "text-embedding-3-large", "version": "1"}
    require(
        cache.get("status") == "complete"
        and cache.get("tasksSha256") == sha(REPO / "docs/testing/hybrid-quality/tasks.json"),
        "cache incomplete or task manifest changed",
    )
    require(cache.get("deploymentBinding", {}).get("model") == expected_model, "cached embedding model differs")
    receipts = cache.get("deploymentReceiptsSha256", {})
    require(set(receipts) == {"deployment-before.json", "deployment-after.json"}, "embedding receipts incomplete")
    bound = {}
    for name, fingerprint in receipts.items():
        receipt_path = checked({"path": str(Path(path).parent / name), "sha256": fingerprint})
        properties = read(receipt_path)["deployment"]["properties"]
        require(
            properties.get("model") == expected_model
            and properties.get("versionUpgradeOption") == "NoAutoUpgrade"
            and properties.get("provisioningState") == "Succeeded",
            "archived embedding deployment differs",
        )
        bound[name] = reference(receipt_path)
    FrozenEmbedder(tasks, cache)
    return cache, bound


def prepare(config_path, output):
    config_path = private_path(config_path)
    config = read(config_path)
    require(config.get("schema") == SCHEMA, "config version differs")
    for flag in ("ownedScopeReady", "freshHealthAccepted", "noOverlappingMeasurements"):
        require(config.get(flag) is True, f"coordinator prerequisite missing: {flag}")
    require(
        config.get("snapshotCapturedAt")
        and 0 <= (datetime.now(UTC) - datetime.fromisoformat(config["snapshotCapturedAt"])).total_seconds() <= 3600,
        "source/readiness snapshot stale",
    )
    refs = {
        name: reference(config[name])
        for name in ("sourceSnapshot", "ownership", "protectedScopes", "queryCache", "healthReceipt")
    }
    snapshot, owner, protected = (
        read(checked(refs["sourceSnapshot"])),
        read(checked(refs["ownership"])),
        read(checked(refs["protectedScopes"]))["libraryIds"],
    )
    source_map = validate_scope(config, snapshot, owner, protected)
    tasks = read(REPO / "docs/testing/hybrid-quality/tasks.json")
    cache, embedding_receipts = validate_cache(checked(refs["queryCache"]), tasks)
    require(len(tasks) == 48, "exact consumed 48-question pack required")
    endpoint = urlsplit(config["endpoint"])
    require(
        endpoint.scheme == "https"
        and endpoint.hostname
        and not endpoint.username
        and not endpoint.password
        and endpoint.path in ("", "/")
        and not endpoint.query,
        "invalid endpoint",
    )
    require(config["database"] and config["targetFingerprint"], "target binding missing")
    dotnet_dll = Path(config["dotnetDll"]).resolve()
    require(dotnet_dll.is_file(), "build exact .NET probe first")
    require(importlib.metadata.version("azure-cosmos") == "4.17.1", "Python SDK version differs")
    root = private_path(output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    queries = {
        t["case"]: {
            "vector": to_float16_query(cache["queries"][t["case"]]["vector"]),
            "terms": lexical_terms(t["question"]),
            "questionSha256": hashlib.sha256(t["question"].encode()).hexdigest(),
        }
        for t in tasks
    }
    require(all(q["terms"] for q in queries.values()), "hybrid would fall back to vector")
    frozen = {
        "schema": SCHEMA,
        "preparedAt": datetime.now(UTC).isoformat(),
        "completedBaselineCommit": BASE,
        "implementationCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "config": reference(config_path),
        "inputs": refs,
        "embeddingReceipts": embedding_receipts,
        "sourceMap": source_map,
        "storedChunkCount": len(snapshot["chunks"]),
        "codeSha256": code_hashes(),
        "dotnetDllSha256": sha(dotnet_dll),
        "tasksSha256": sha(REPO / "docs/testing/hybrid-quality/tasks.json"),
        "manifestSha256": sha(REPO / "docs/testing/hybrid-quality/manifest.json"),
        "pythonVersion": importlib.metadata.version("azure-cosmos"),
        "queries": queries,
        "schedule": schedule(list(queries)),
        "limits": {
            "warmupQueries": 6,
            "exactReferenceQueries": 384,
            "measuredQueries": 576,
            "wholeRunSeconds": DEADLINE_SECONDS,
            "wireRequestsPerSdk": REQUEST_BUDGET,
            "logicalQuerySeconds": 90,
            "maxPagesPerQuery": 32,
            "throttleRetriesPerSdk": 0,
        },
        "semantics": {
            "candidateTop": 16,
            "postApiTop": None,
            "neighborsExpanded": False,
            "gatewayBoth": True,
            "queryConcurrency": 1,
            "gatewayConnectionsPerHost": 1,
            "dotnetMaxConcurrency": 1,
            "dotnetMaxBufferedItemCount": 16,
            "pythonPipeline": "SDK default; one HTTP connection",
            "context": "consumed exploratory/regression pack, no model answering",
            "exactHybridOracle": None,
            "fullTextScoreScope": "service default",
            "rrfWeights": "service default",
            "applicationRoutingChanged": False,
        },
    }
    save(root / "controls.json", frozen)
    print(
        json.dumps(
            {
                "status": "prepared",
                "queries": 48,
                "measuredQueries": 576,
                "cloudCalls": 0,
                "controlsSha256": sha(root / "controls.json"),
            }
        )
    )


class FileCredential:
    def __init__(self, path):
        value = read(private_path(path))
        self.token = AccessToken(value["accessToken"], int(value["expires_on"]))
        require(
            self.token.expires_on > time.time() + DEADLINE_SECONDS + 120,
            "shared token needs entire bounded window plus120seconds",
        )

    async def get_token(self, *scopes, **kwargs):
        require(self.token.expires_on > time.time() + 120, "shared credential near expiry")
        return self.token


class WireCapture(AioHttpTransport):
    def __init__(self, endpoint, database, root):
        super().__init__(
            session=aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=1, limit_per_host=1)), session_owner=True
        )
        self.endpoint, self.database, self.root = urlsplit(endpoint), database, root
        self.current = "startup"
        self.sequence = 0
        self.events = []

    async def send(self, request, **kwargs):
        uri = urlsplit(request.url)
        require(
            (uri.scheme, uri.hostname, uri.port or 443)
            == (self.endpoint.scheme, self.endpoint.hostname, self.endpoint.port or 443),
            "unexpected data endpoint",
        )
        headers = {str(k).lower(): str(v) for k, v in request.headers.items()}
        query = (
            request.method == "POST"
            and uri.path.rstrip("/") == f"/dbs/{quote(self.database, safe='')}/colls/chunks/docs"
            and any(
                k in headers
                for k in ("x-ms-documentdb-isquery", "x-ms-cosmos-is-query-plan-request", "x-ms-documentdb-isqueryplan")
            )
        )
        require(request.method == "GET" or query, "mutation or unknown operation blocked")
        require(self.sequence < REQUEST_BUDGET, "Python wire budget exhausted")
        self.sequence += 1
        body = request.body
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body) if body is not None else ""
        if isinstance(body, bytes):
            body = body.decode()
        row = {
            "sequence": self.sequence,
            "logicalId": self.current,
            "method": request.method,
            "url": request.url,
            "requestHeaders": selected_headers(request.headers),
            "requestBody": body,
            "requestBodySha256": hashlib.sha256(body.encode()).hexdigest(),
            "startedAt": datetime.now(UTC).isoformat(),
        }
        started = time.perf_counter()
        try:
            kwargs.update(connection_timeout=10, read_timeout=30)
            response = await asyncio.wait_for(super().send(request, **kwargs), 40)
            await asyncio.wait_for(response.load_body(), 15)
            data = response.body()
            require(len(data) <= 8_000_000, "response capture bound exceeded")
            row.update(
                httpStatus=response.status_code,
                responseHeaders=selected_headers(response.headers),
                responseBody=data.decode(),
                responseBodySha256=hashlib.sha256(data).hexdigest(),
            )
            return response
        except BaseException as error:
            row.update(errorType=type(error).__name__, errorMessage=str(error))
            raise
        finally:
            row["totalMs"] = (time.perf_counter() - started) * 1000
            self.events.append(row)
            save(self.root / f"wire-{row['sequence']:06}.json", row)


def zero_retries(client):
    policy = client.client_connection.connection_policy
    policy.RetryOptions._max_retry_attempt_count = 0
    policy.RetryOptions._max_wait_time_in_seconds = 0
    require(
        policy.RetryOptions.MaxRetryAttemptCount == policy.RetryOptions.MaxWaitTimeInSeconds == 0,
        "throttle retry settings differ",
    )
    require(
        all(
            getattr(policy.ConnectionRetryConfiguration, key) == 0
            for key in ("total_retries", "connect_retries", "read_retries", "status_retries")
        ),
        "connection retries remain",
    )


async def python_query(container, recorder, command):
    recorder.current = command["id"]
    count_before = recorder.sequence
    row = {"id": command["id"], "sdk": "python", "status": "partial"}
    started = time.perf_counter()
    try:
        kwargs = {
            "parameters": command["parameters"],
            "max_item_count": TOP,
            "populate_query_metrics": True,
            "populate_index_metrics": True,
        }
        if command["partitionKey"] is not None:
            kwargs["partition_key"] = command["partitionKey"]
        result = []
        pages = 0
        async with asyncio.timeout(90):
            async for page in container.query_items(command["sql"], **kwargs).by_page():
                pages += 1
                require(pages <= 32, "page budget exceeded")
                async for item in page:
                    result.append(item)
                    require(len(result) <= 1000, "row bound exceeded")
        row.update(status="complete", rows=result, sdkPageRequestCharge=None, pages=pages)
    except Exception as error:
        row.update(
            errorType=type(error).__name__,
            errorMessage=str(error),
            httpStatus=getattr(error, "status_code", None),
            substatus=getattr(error, "sub_status", None),
        )
    row.update(totalMs=(time.perf_counter() - started) * 1000, recordedWireRequests=recorder.sequence - count_before)
    save(recorder.root / (command["id"] + "-result.json"), row)
    return row


async def live_snapshot(client, container, recorder, config, expected, phase):
    recorder.current = "snapshot-" + phase
    db = client.get_database_client(config["database"])
    library = await db.get_container_client("libraries").read_item(
        config["libraryId"], partition_key=config["libraryId"]
    )
    require(
        library.get("status") == "active"
        and library.get("ingestionProfile") == expected["library"]["ingestionProfile"]
        and library.get("createdBy") == expected["library"].get("createdBy"),
        "live library ownership/profile changed",
    )
    docs = []
    for d in expected["documents"]:
        actual = await db.get_container_client("documents").read_item(d["id"], partition_key=config["libraryId"])
        for key in (
            "sha256",
            "fileName",
            "generation",
            "activeGeneration",
            "metadataVersion",
            "ingestionJob",
            "fullText",
            "chunkCount",
            "author",
            "tags",
            "periodStart",
            "periodEnd",
        ):
            require(actual.get(key) == d.get(key), f"live document {key} changed")
        require(actual.get("status") == "ready" and not actual.get("pendingGeneration"), "live document not ready")
        docs.append(actual)
    rows = [
        v
        async for v in container.query_items(
            INVENTORY_SQL,
            parameters=[{"name": "@libraryId", "value": config["libraryId"]}],
            partition_key=[config["libraryId"]],
            max_item_count=100,
        )
    ]
    rows.sort(key=lambda c: (c["documentId"], c["id"]))
    require(rows == expected["chunks"], "live stored rows differ from frozen snapshot")
    return {"library": library, "documents": docs, "chunks": rows}


async def run(state, credential_file):
    root = private_path(state)
    controls = read(root / "controls.json")
    verify_benchmark(controls)
    require(controls["codeSha256"] == code_hashes(), "measurement code drift")
    config = read(checked(controls["config"]))
    require(sha(config["dotnetDll"]) == controls["dotnetDllSha256"], ".NET build drift")
    require(importlib.metadata.version("azure-cosmos") == "4.17.1", "Python SDK drift")
    refs = {key: checked(ref) for key, ref in controls["inputs"].items()}
    for ref in controls["embeddingReceipts"].values():
        checked(ref)
    snapshot = read(refs["sourceSnapshot"])
    validate_scope(config, snapshot, read(refs["ownership"]), read(refs["protectedScopes"])["libraryIds"])
    credential = FileCredential(credential_file)
    save(
        root / "execution.json",
        {
            "startedAt": datetime.now(UTC).isoformat(),
            "controlsSha256": sha(root / "controls.json"),
            "automaticApplicationRetries": False,
        },
    )
    py_root = root / "python"
    py_root.mkdir(mode=0o700)
    recorder = WireCapture(config["endpoint"], config["database"], py_root)
    dotnet_config = {
        **config,
        "credentialFile": str(private_path(credential_file)),
        "dotnetRawDirectory": str(root / "dotnet"),
    }
    save(root / "dotnet-config-private.json", dotnet_config)
    stderr = (root / "dotnet-stderr.log").open("x")
    process = await asyncio.create_subprocess_exec(
        config["dotnetExecutable"],
        config["dotnetDll"],
        str(root / "dotnet-config-private.json"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env={**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false"},
    )
    rows, exact_rows, warm_rows = [], [], []
    try:
        hello = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
        require(
            hello.get("status") == "ready" and hello.get("version", "").startswith("3.63.0"),
            ".NET process not exact version",
        )
        save(root / "dotnet-loaded.json", hello)
        client = CosmosClient(
            config["endpoint"],
            credential=credential,
            transport=recorder,
            enable_endpoint_discovery=False,
            retry_total=0,
            retry_connect=0,
            retry_read=0,
            retry_status=0,
            retry_on_status_codes=[],
        )
        zero_retries(client)
        async with asyncio.timeout(DEADLINE_SECONDS), client:
            container = client.get_database_client(config["database"]).get_container_client("chunks")
            recorder.current = "policy-before"
            properties = await container.read()
            require(
                not definition_mismatches(read(REPO / "cosmos/chunks.container.json"), properties),
                "container policies differ",
            )
            require(
                target_fingerprint(config["endpoint"], config["database"], "chunks", properties["_rid"])
                == config["targetFingerprint"],
                "container incarnation changed",
            )
            save(root / "container-before-private.json", properties)
            before = await live_snapshot(client, container, recorder, config, snapshot, "before")
            save(root / "source-before-private.json", before)

            async def execute(sdk, command):
                save(root / (command["id"] + "-command.json"), command)
                if sdk == "python":
                    result = await python_query(container, recorder, command)
                else:
                    process.stdin.write((json.dumps(command, allow_nan=False) + "\n").encode())
                    await process.stdin.drain()
                    reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 100))
                    require(reply["id"] == command["id"], ".NET protocol id differs")
                    result = read(checked({"path": reply["resultPath"], "sha256": reply["sha256"]}))
                return result

            for case, query in controls["queries"].items():
                for index, d in enumerate(snapshot["documents"]):
                    command = make_command(
                        case,
                        "vector-prefix",
                        config["libraryId"],
                        query,
                        identifier=f"exact-{case}-d{index}",
                        document=d["id"],
                        reference_query=True,
                    )
                    result = await execute("python", command)
                    result.update(case=case, documentId=d["id"])
                    require(result["status"] == "complete", "exact reference query failed")
                    require(len(result["rows"]) == d["chunkCount"] <= TOP, "reference did not cover complete document")
                    exact_rows.append(result)
            save(root / "exact-references-private.json", exact_rows)
            query = next(iter(controls["queries"].values()))
            for sdk, route in CELLS:
                command = make_command("hq-01", route, config["libraryId"], query, identifier=f"warm-{sdk}-{route}")
                warm_rows.append(await execute(sdk, command))
            save(root / "warmups-private.json", warm_rows)
            for item in controls["schedule"]:
                require(code_hashes() == controls["codeSha256"], "measurement code drift during schedule")
                command = make_command(
                    item["case"],
                    item["route"],
                    config["libraryId"],
                    controls["queries"][item["case"]],
                    identifier=item["id"],
                )
                row = await execute(item["sdk"], command)
                row.update(item)
                rows.append(row)
                # Preserve each logical failure once; continue only this fixed matrix.
                save(root / (item["id"] + "-receipt.json"), row)
            recorder.current = "policy-after"
            after_properties = await container.read()
            require(after_properties == properties, "container changed during schedule")
            save(root / "container-after-private.json", after_properties)
            after = await live_snapshot(client, container, recorder, config, snapshot, "after")
            save(root / "source-after-private.json", after)
            require(before == after, "source changed during schedule")
            require(code_hashes() == controls["codeSha256"], "code drift after schedule")
            verify_benchmark(controls)
        process.stdin.write(b'{"op":"stop"}\n')
        await process.stdin.drain()
        await asyncio.wait_for(process.wait(), 10)
        require(process.returncode == 0, ".NET worker exit failed")
        stderr.flush()
        save(
            root / "capture-private.json",
            {
                "status": "complete",
                "rows": rows,
                "finishedAt": datetime.now(UTC).isoformat(),
                "unchangedSourceAndPolicies": True,
            },
        )
        save(
            root / "finalization.json",
            {
                "status": "complete",
                "controlsSha256": sha(root / "controls.json"),
                "artifactsSha256": {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*.json"))},
            },
        )
        print(
            json.dumps(
                {
                    "status": "complete",
                    "measuredQueries": len(rows),
                    "exactReferenceQueries": len(exact_rows),
                    "warmupQueries": len(warm_rows),
                    "logicalFailures": sum(v["status"] != "complete" for v in rows),
                }
            )
        )
    except BaseException as error:
        save(
            root / "failure-private.json",
            {
                "status": "partial",
                "errorType": type(error).__name__,
                "message": str(error),
                "completedMeasured": len(rows),
                "completedExactReferences": len(exact_rows),
            },
        )
        raise
    finally:
        if process.returncode is None:
            process.stdin.write(b'{"op":"stop"}\n')
            try:
                await process.stdin.drain()
                await asyncio.wait_for(process.wait(), 10)
            except (BrokenPipeError, TimeoutError, ConnectionResetError):
                process.kill()
                await process.wait()
        stderr.close()


def logical_wire_summary(events):
    """Measured response facts; matching signatures are not inferred total retries."""
    statuses = Counter()
    data, plans, metadata, charges = [], [], [], []
    for event in events:
        status = event.get("httpStatus")
        if status is not None:
            statuses[str(status)] += 1
        request = {k.lower(): str(v) for k, v in event["requestHeaders"].items()}
        response = {k.lower(): str(v) for k, v in event.get("responseHeaders", {}).items()}
        is_plan = any(
            request.get(k, "").lower() == "true"
            for k in ("x-ms-cosmos-is-query-plan-request", "x-ms-documentdb-isqueryplan")
        )
        target = plans if is_plan else data if event["method"] == "POST" else metadata
        target.append(event)
        if "x-ms-request-charge" in response:
            charges.append(float(response["x-ms-request-charge"]))
    ranges = set()
    missing_range = 0
    signatures = []
    for event in data:
        req = {k.lower(): str(v) for k, v in event["requestHeaders"].items()}
        res = {k.lower(): str(v) for k, v in event.get("responseHeaders", {}).items()}
        if 200 <= event.get("httpStatus", 0) < 300:
            range_id = res.get("x-ms-documentdb-partitionkeyrangeid") or req.get("x-ms-documentdb-partitionkeyrangeid")
            if range_id:
                ranges.add(range_id)
            else:
                missing_range += 1
        signatures.append(
            digest(
                {
                    "body": event["requestBodySha256"],
                    "url": event["url"],
                    "route": {
                        k: v for k, v in req.items() if "partition" in k or "epk" in k or k == "x-ms-continuation"
                    },
                }
            )
        )
    return {
        "wireRequests": len(events),
        "httpStatusCounts": dict(statuses),
        "dataQueryRequests": len(data),
        "queryPlanRequests": len(plans),
        "metadataRequests": len(metadata),
        "knownWireRequestCharge": sum(charges),
        "responsesWithCharge": len(charges),
        "responsesWithoutCharge": len(events) - len(charges),
        "totalBilledRu": None,
        "visibleDataRangeIdsPrivate": sorted(ranges),
        "successfulDataResponsesWithoutRange": missing_range,
        "dataRequestsWithLogicalPartitionHeader": sum(
            "x-ms-documentdb-partitionkey" in e["requestHeaders"] for e in data
        ),
        "dataRequestsWithEffectivePartitionBounds": sum(
            any("epk" in k.lower() for k in e["requestHeaders"]) for e in data
        ),
        "dataRequestsWithPhysicalRangeHeader": sum(
            "x-ms-documentdb-partitionkeyrangeid" in e["requestHeaders"] for e in data
        ),
        "queryPlanFallback4001004": sum(
            e.get("httpStatus") == 400 and str(e.get("responseHeaders", {}).get("x-ms-substatus")) == "1004"
            for e in data
        ),
        "visibleThrottleResponses": statuses["429"],
        "visibleRetryAfterResponses": sum(
            any(k.lower() in ("retry-after", "x-ms-retry-after-ms") for k in e.get("responseHeaders", {}))
            for e in events
        ),
        "repeatedDataRequestSignatures": sum(v - 1 for v in Counter(signatures).values()),
        "sdkTotalRetries": None,
        "instrumentedWireElapsedSumMs": sum(e["totalMs"] for e in events),
        "errorEvents": sum("errorType" in e for e in events),
    }


def exact_scores(rows, expected):
    by_case = defaultdict(dict)
    expected_ids = {c["id"] for c in expected["chunks"]}
    for result in rows:
        require(result["status"] == "complete", "missing exact reference")
        previous = math.inf
        for row in result["rows"]:
            require(
                row["id"] in expected_ids and row["documentId"] == result["documentId"] and row["generation"] == 1,
                "exact reference foreign source",
            )
            score = row.get("score")
            require(isinstance(score, (int, float)) and math.isfinite(score), "missing/nonfinite exact score")
            require(score <= previous + SCORE_EPSILON, "exact score order differs from descending cosine")
            previous = score
            require(row["id"] not in by_case[result["case"]], "duplicate exact id")
            by_case[result["case"]][row["id"]] = score
    require(
        len(by_case) == 48 and all(set(scores) == expected_ids for scores in by_case.values()),
        "incomplete exact references",
    )
    return by_case


def vector_accuracy(ids, scores, k):
    cutoff = sorted(scores.values(), reverse=True)[k - 1]
    mandatory = {key for key, value in scores.items() if value > cutoff + SCORE_EPSILON}
    eligible = {key for key, value in scores.items() if value >= cutoff - SCORE_EPSILON}
    found = set(ids[:k])
    recall = (len(found & mandatory) + min(k - len(mandatory), len(found & (eligible - mandatory)))) / k
    valid = all(key in scores for key in ids)
    ordered = valid and all(scores[a] + SCORE_EPSILON >= scores[b] for a, b in zip(ids, ids[1:], strict=False))
    return {"tieAwareRecall": recall, "descendingExactScoreOrder": ordered}


def document_coverage(ids, known, source_map, task):
    """Document presence only; never claim containing the required passage or fact."""
    expected = set(task["expectedSourceIds"])
    found = {source_map[known[key]["documentId"]] for key in ids if key in known}
    return len(found & expected) / len(expected) if task["answerable"] and expected else None


def summary(state, output):
    root = private_path(state)
    final = read(root / "finalization.json")
    controls = read(root / "controls.json")
    verify_benchmark(controls)
    require(final["status"] == "complete" and final["controlsSha256"] == sha(root / "controls.json"), "not finalized")
    for name, fingerprint in final["artifactsSha256"].items():
        require(sha(root / name) == fingerprint, "finalized artifact changed")
    capture = read(root / "capture-private.json")
    expected = read(checked(controls["inputs"]["sourceSnapshot"]))
    scores = exact_scores(read(root / "exact-references-private.json"), expected)
    tasks = {t["case"]: t for t in read(REPO / "docs/testing/hybrid-quality/tasks.json")}
    known = {c["id"]: c for c in expected["chunks"]}
    wires = defaultdict(list)
    for sdk in ("python", "dotnet"):
        for path in sorted((root / sdk).glob("wire-*.json")):
            wire = read(path)
            # .NET serializes explicit names identically, including lower-case headers.
            for field in ("requestHeaders", "responseHeaders"):
                wire[field] = {k.lower(): str(v) for k, v in wire.get(field, {}).items()}
            wires[sdk, wire["logicalId"]].append(wire)
    placement = defaultdict(set)
    for result in read(root / "exact-references-private.json"):
        placement[result["documentId"]].update(
            logical_wire_summary(wires["python", result["id"]])["visibleDataRangeIdsPrivate"]
        )
    known_library_ranges = set().union(*placement.values())
    placement_complete = len(placement) == 8 and all(placement.values())
    rows, private_ids = [], {}
    for result in capture["rows"]:
        ids = [r["id"] for r in result.get("rows", [])]
        valid = len(ids) == TOP and len(set(ids)) == TOP and all(key in known for key in ids)
        valid = valid and all(
            r.get("documentId") == known[r["id"]]["documentId"]
            and r.get("generation") == 1
            and r.get("chunkIndex") == known[r["id"]]["chunkIndex"]
            for r in result.get("rows", [])
            if r["id"] in known
        )
        wire = logical_wire_summary(wires[result["sdk"], result["id"]])
        ranges = set(wire.pop("visibleDataRangeIdsPrivate"))
        private_ids[result["sdk"], result["route"], result["case"], result["repeat"]] = ids
        rows.append(
            {
                "case": result["case"],
                "sdk": result["sdk"],
                "route": result["route"],
                "repeat": result["repeat"],
                "status": result["status"],
                "cardinalityAndSourceIsolationPassed": valid,
                "topCandidates": [
                    {"source": controls["sourceMap"][known[key]["documentId"]], "chunkIndex": known[key]["chunkIndex"]}
                    for key in ids
                    if key in known
                ],
                "expectedDocumentCoverage": {
                    str(k): document_coverage(ids[:k], known, controls["sourceMap"], tasks[result["case"]])
                    for k in (8, 16)
                },
                "candidateRankListSha256": digest(ids),
                "vectorAt8": vector_accuracy(ids, scores[result["case"]], 8)
                if result["route"] == "vector-prefix" and valid
                else None,
                "vectorAt16": vector_accuracy(ids, scores[result["case"]], 16)
                if result["route"] == "vector-prefix" and valid
                else None,
                "exactHybridRankingCorrectness": None,
                "instrumentedLogicalMs": result["totalMs"],
                "sdkPageRequestCharge": result.get("sdkPageRequestCharge"),
                "visibleDataRangeCount": len(ranges),
                "positiveContactsOutsideKnownLibraryRanges": len(ranges - known_library_ranges)
                if placement_complete
                else None,
                "rangeVisibilityCompleteForSuccessfulDataResponses": wire["successfulDataResponsesWithoutRange"] == 0,
                **wire,
            }
        )
    require(
        len(rows) == 576
        and {(r["sdk"], r["route"], r["case"], r["repeat"]) for r in rows}
        == {(s["sdk"], s["route"], s["case"], s["repeat"]) for s in controls["schedule"]},
        "matrix incomplete",
    )
    pairs = []
    for case in controls["queries"]:
        for repeat in (1, 2):
            for left, right in (
                (("python", "vector-prefix"), ("dotnet", "vector-prefix")),
                (("python", "hybrid-where"), ("dotnet", "hybrid-where")),
                (("python", "hybrid-prefix"), ("python", "hybrid-where")),
                (("dotnet", "hybrid-prefix"), ("dotnet", "hybrid-where")),
            ):
                a = private_ids[*left, case, repeat]
                b = private_ids[*right, case, repeat]
                pairs.append(
                    {
                        "case": case,
                        "repeat": repeat,
                        "left": "/".join(left),
                        "right": "/".join(right),
                        "bothCompleteTop16": len(a) == len(b) == TOP,
                        "top16SetOverlap": len(set(a) & set(b)) / TOP,
                        "exactOrderAgreement": a == b,
                        "agreementIsExactHybridOracle": False,
                    }
                )
    cells = {}
    for sdk, route in CELLS:
        group = [r for r in rows if r["sdk"] == sdk and r["route"] == route]
        cells[sdk + "/" + route] = {
            "queries": len(group),
            "completeQueries": sum(r["status"] == "complete" for r in group),
            "sourceIsolationPasses": sum(r["cardinalityAndSourceIsolationPassed"] for r in group),
            "distinctRankListsByRepeat": {
                str(rep): len({r["candidateRankListSha256"] for r in group if r["repeat"] == rep}) for rep in (1, 2)
            },
            "allQueriesSameRankingByRepeat": {
                str(rep): len({r["candidateRankListSha256"] for r in group if r["repeat"] == rep}) == 1
                for rep in (1, 2)
            },
            "knownWireRu": sum(r["knownWireRequestCharge"] for r in group),
            "totalBilledRu": None,
            "meanKnownWireRu": statistics.mean(r["knownWireRequestCharge"] for r in group),
            "logicalMsMedian": statistics.median(r["instrumentedLogicalMs"] for r in group),
            "logicalMsMean": statistics.mean(r["instrumentedLogicalMs"] for r in group),
            "wireRequests": sum(r["wireRequests"] for r in group),
            "queryPlanRequests": sum(r["queryPlanRequests"] for r in group),
            "visible429Responses": sum(r["visibleThrottleResponses"] for r in group),
            "positiveOutsideLibraryRangeQueries": sum(
                bool(r["positiveContactsOutsideKnownLibraryRanges"]) for r in group
            ),
            "meanVectorRecallAt16": statistics.mean(r["vectorAt16"]["tieAwareRecall"] for r in group if r["vectorAt16"])
            if route == "vector-prefix" and any(r["vectorAt16"] for r in group)
            else None,
        }
    warm = read(root / "warmups-private.json")
    phase_usage = {}
    for phase, logical_ids in (
        ("exactReferences", {r["id"] for r in read(root / "exact-references-private.json")}),
        ("warmups", {r["id"] for r in warm}),
        (
            "snapshotsAndMetadata",
            {key[1] for key in wires if key[1].startswith(("snapshot-", "policy-", "startup", "background"))},
        ),
    ):
        events = [event for (sdk, logical), es in wires.items() if logical in logical_ids for event in es]
        usage = logical_wire_summary(events)
        usage.pop("visibleDataRangeIdsPrivate")
        phase_usage[phase] = usage
    exported = {
        "schema": SCHEMA + ".summary",
        "status": "complete_candidate_for_review",
        "completedBaselineCommit": BASE,
        "scope": {
            "queries": 48,
            "repeats": 2,
            "measuredQueries": 576,
            "warmupQueries": 6,
            "exactReferenceQueries": 384,
            "originalDocuments": 8,
            "chunks": len(expected["chunks"]),
            "libraryPlacementVisibleRangeCount": len(known_library_ranges),
            "libraryPlacementObservedForEveryDocument": placement_complete,
            "freshConfirmation": False,
            "modelsUsed": 0,
        },
        "controlsSha256": sha(root / "controls.json"),
        "finalizationSha256": sha(root / "finalization.json"),
        "candidateRows": rows,
        "pairedRouteAndSdkAgreement": pairs,
        "byCell": cells,
        "separatePreparationUsage": phase_usage,
        "limits": [
            (
                "David Ames's SQL, request options and partition layout are unknown; this is a controlled SDK "
                "comparison, not full replication."
            ),
            (
                "Query-dependent ranking, source isolation and cross-client/route agreement are separate from "
                "exact hybrid order and semantic recall; neither agreement nor distinctness alone proves exact "
                "hybrid correctness."
            ),
            (
                "Exact vector references merge all eight full-document partitions using brute force; descending "
                "cosine similarity and cutoff ties are explicitly handled."
            ),
            (
                "Visible data-range contacts are positive evidence; missing range headers mean unknown scope, "
                "never zero fan-out. A single-range target cannot prove improved large-container routing "
                "efficiency."
            ),
            (
                "Known wire RU includes captured charges only. SDK page RU overlaps wire RU and must not be "
                "added. Missing charges and total billed RU remain unknown."
            ),
            (
                "Matched throttle retries are zero; SDK metadata refresh/query-plan fallback is not an "
                "application retry. Other SDK-native internal retry paths can differ and total retries remain "
                "null."
            ),
            (
                "Gateway, one logical query at a time and bounded connection/page controls are fixed. "
                "Instrumented latency includes diagnostics capture and SDK runtime differences; no production "
                "latency or concurrency claim."
            ),
            (
                "Small-corpus index engagement is not assumed. No account/container provisioning, application "
                "deployment/default change, representation change, model call or data mutation is performed by "
                "this harness."
            ),
        ],
    }
    out = Path(output).resolve()
    require(not out.exists(), "summary output already exists")
    out.write_text(json.dumps(exported, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": "summarized", "measuredQueries": 576, "rows": len(rows), "sha256": sha(out)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "run", "summarize"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    logging.getLogger("azure").setLevel(logging.CRITICAL)
    if args.operation == "prepare":
        require(args.config and args.output, "prepare requires config and new output directory")
        prepare(args.config, args.output)
    elif args.operation == "run":
        require(args.state and args.credential_file, "run requires state and private shared credential")
        asyncio.run(run(args.state, args.credential_file))
    else:
        require(args.state and args.output, "summarize requires finalized state and new output file")
        summary(args.state, args.output)


if __name__ == "__main__":
    main()
