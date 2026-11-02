"""Bounded R1 routing diagnostic using immutable real-text canary inputs.

No application, representation or index-policy changes. ``prepare`` is local;
``load`` and ``measure`` require the coordinator's isolated cloud reservation.
The raw capture and all state are private. ``report`` exports counts/hashes only.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fli.query import SearchFilters, check_query, lexical_terms, render_search, to_float16_query
from scripts.canary import (
    RECALL_FLOOR,
    K,
    RawTally,
    SearchMeasurement,
    Throttle,
    definition_mismatches,
    exact_variant,
    run_query,
    scored_variant,
    target_fingerprint,
    wait_for_index,
    write_all,
)
from scripts.retrieval_r0_corpus import private_path

REPO = Path(__file__).resolve().parents[1]
BASE = "a449dc01e5acde6391bfc1aa50ca07670d54097d"
EPSILON = 1e-6
QUERIES = 20
REPEATS = 3
CODE = (
    "scripts/retrieval_r1.py",
    "scripts/canary.py",
    "src/fli/query.py",
    "cosmos/chunks.container.json",
    "requirements-local.lock",
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(value, handle, allow_nan=False)
    temporary.replace(path)


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def code_hashes() -> dict[str, str]:
    return {name: digest((REPO / name).read_bytes()) for name in CODE}


def finite(value: Any) -> bool:
    return type(value) in (float, int) and math.isfinite(value)


def validate_capture(capture: dict, receipt: dict, raw: bytes) -> None:
    if not receipt.get("passed") or receipt.get("inputCaptureSha256") != digest(raw):
        raise ValueError("capture is not bound to a passing canary")
    if capture.get("targetFingerprint") != receipt.get("targetFingerprint"):
        raise ValueError("capture target differs from canary")
    if capture.get("embedding") != {"model": "text-embedding-3-large", "revision": "1", "dimensions": 3072}:
        raise ValueError("embedding identity differs from frozen R0 control")
    rows = capture.get("chunks", [])
    if len(rows) < 1200 or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("capture must contain 1200+ unique real chunks")
    if any(
        not row.get("text") or len(row.get("embedding", [])) != 3072 or not all(finite(v) for v in row["embedding"])
        for row in rows
    ):
        raise ValueError("capture contains invalid source/vector content")
    if len(capture.get("vectorQueries", [])) != QUERIES or len(capture.get("hybridQueries", [])) != QUERIES:
        raise ValueError("capture must contain 20 vector and 20 hybrid source queries")
    if len(capture.get("queryVectors", [])) != QUERIES * 2:
        raise ValueError("missing query vectors")
    if any(len(v) != 3072 or not all(finite(x) for x in v) for v in capture["queryVectors"]):
        raise ValueError("invalid query vector")
    known_docs = {d["documentId"] for d in capture["documents"] if d.get("chunks")}
    if any(row["documentId"] not in known_docs or row.get("generation") != 1 for row in rows):
        raise ValueError("source document or generation binding mismatch")


def prepare(args: argparse.Namespace) -> None:
    if any(args.state.iterdir()):
        raise ValueError("prepare requires a fresh state directory")
    raw = private_path(args.capture).read_bytes()
    capture, receipt = json.loads(raw), read(private_path(args.canary))
    validate_capture(capture, receipt, raw)
    library_id = f"r1-{uuid.uuid4().hex}"
    # Only the test library identity changes; exact chunk text/order/vectors stay fixed.
    capture["chunks"] = [dict(row, libraryId=library_id) for row in capture["chunks"]]
    capture["libraryId"] = library_id
    save(args.state / "inputs.json", capture)
    save(
        args.state / "controls.json",
        {
            "schema": "fli.retrieval-r1-controls.v1",
            "baselineCommit": BASE,
            "sourceCaptureSha256": digest(raw),
            "inputsSha256": digest((args.state / "inputs.json").read_bytes()),
            "canarySha256": digest(private_path(args.canary).read_bytes()),
            "targetFingerprint": receipt["targetFingerprint"],
            "queries": QUERIES,
            "repeats": REPEATS,
            "k": K,
            "recallFloor": RECALL_FLOOR,
            "scoreTieEpsilon": EPSILON,
            "minimumUsefulEfficiencyImprovement": 0.20,
            "qualityTolerance": "zero isolation/order/cardinality violations; each repeat mean recall >= 0.95",
            "changedVariable": (
                "query route only; prefix ANN vs no-prefix ANN; independently merged full-key exact truth"
            ),
            "hybrid": (
                "20 different source-derived queries; equality/no partition key; "
                "rank distinctness only, no exact-hybrid recall"
            ),
            "codeHashes": code_hashes(),
            "createdAt": datetime.now(UTC).isoformat(),
        },
    )
    print("Prepared immutable R1 source/vector/query inputs; no cloud call.")


def frozen(state: Path) -> tuple[dict, dict]:
    controls, inputs = read(state / "controls.json"), read(state / "inputs.json")
    if digest((state / "inputs.json").read_bytes()) != controls["inputsSha256"]:
        raise ValueError("frozen inputs changed")
    if controls["codeHashes"] != code_hashes():
        raise ValueError("measurement implementation changed after freeze; create a new named run")
    return controls, inputs


async def clients(state: Path, receipt_path: Path):
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    controls, inputs = frozen(state)
    receipt = read(private_path(receipt_path))
    if digest(private_path(receipt_path).read_bytes()) != controls["canarySha256"] or not receipt.get("passed"):
        raise ValueError("passing canary changed after freeze")
    credential = DefaultAzureCredential()
    cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=credential)
    try:
        database_name = os.environ["COSMOS_DATABASE"]
        container = cosmos.get_database_client(database_name).get_container_client("chunks")
        actual = await container.read()
        fingerprint = target_fingerprint(os.environ["COSMOS_ENDPOINT"], database_name, "chunks", actual["_rid"])
        if fingerprint != controls["targetFingerprint"]:
            raise ValueError("cloud target is not the canary-bound container incarnation")
        expected = read(REPO / "cosmos/chunks.container.json")
        if definition_mismatches(expected, actual):
            raise ValueError("container policy differs from baseline")
    except BaseException:
        await cosmos.close()
        await credential.close()
        raise
    return credential, cosmos, container, controls, inputs


async def load(args: argparse.Namespace) -> None:
    credential, cosmos, container, controls, inputs = await clients(args.state, args.canary)
    target = args.state / "load.json"
    try:
        if target.exists():
            raise ValueError("load has already been attempted; inspect its receipt before any resume")
        library = inputs["libraryId"]
        existing = [
            x
            async for x in container.query_items(
                "SELECT TOP 1 c.id FROM c WHERE c.libraryId = @libraryId",
                parameters=[{"name": "@libraryId", "value": library}],
                partition_key=[library],
            )
        ]
        if existing:
            raise ValueError("new R1 library is not empty")
        save(target, {"status": "started", "inputDigest": controls["inputsSha256"]})
        throttle = Throttle()
        written = await write_all(container, inputs["chunks"], throttle, lambda _n: None)
        save(
            target,
            {
                "status": "complete",
                "inputDigest": controls["inputsSha256"],
                "rows": len(inputs["chunks"]),
                "knownWriteRU": written,
                "writeRUComplete": False,
                "costLimit": (
                    "existing writer counts successful response charges only; failed/SDK retry charges unavailable"
                ),
                "applicationRetries": throttle.count,
                "applicationRetryWaitMs": throttle.wait_seconds * 1000,
            },
        )
    finally:
        await cosmos.close()
        await credential.close()
    print("Loaded new R1 scope; original canary/R0 scopes unchanged.")


def diagnostic_query(
    library: str,
    vector: list[float],
    *,
    document: str | None = None,
    terms: list[str] | None = None,
    exact: bool = False,
):
    rendered = render_search(
        library, to_float16_query(vector), terms or [], SearchFilters(), K, document_id=document, generation=1
    )
    sql = scored_variant(rendered.sql)
    # Narrow projection preserves real distances and source bindings without returning raw content.
    projection, rest = sql.split(" FROM c ", 1)
    distance = projection.split(" AS score, ", 1)[0]
    sql = distance + " AS score, c.id, c.libraryId, c.documentId, c.generation FROM c " + rest
    if exact:
        sql = exact_variant(sql)
    # check_query deliberately rejects *any* embedding reference in a projection;
    # the canary's measured-distance expression is its one test-only extension.
    check_query("SELECT TOP 10 c.id, c.libraryId, c.documentId, c.generation FROM c " + sql.split(" FROM c ", 1)[1])
    return sql, rendered.parameters


def merged_exact(
    document_results: dict[str, dict], expected_counts: dict[str, int], allowed: dict[str, dict]
) -> list[tuple[str, float]]:
    if set(document_results) != set(expected_counts):
        raise ValueError("independent exact reference is missing a document partition")
    pairs: list[tuple[str, float]] = []
    for document, row in document_results.items():
        ids, scores = row["result_ids"], row["scores"]
        if row.get("error") or row.get("null_scores") or len(ids) != min(K, expected_counts[document]):
            raise ValueError("incomplete exact document result")
        if len(ids) != len(scores) or len(set(ids)) != len(ids) or not all(finite(s) for s in scores):
            raise ValueError("invalid exact distances or repeated IDs")
        bindings = row.get("result_bindings", [])
        if (
            len(bindings) != len(ids)
            or [item.get("id") for item in bindings] != ids
            or any(
                item.get("id") not in allowed
                or item.get("documentId") != document
                or any(item.get(key) != allowed[item["id"]][key] for key in ("libraryId", "documentId", "generation"))
                for item in bindings
            )
        ):
            raise ValueError("independent exact reference has invalid source bindings")
        if any(a > b + EPSILON for a, b in zip(scores, scores[1:], strict=False)):
            raise ValueError("document exact result is not distance ordered")
        pairs.extend(zip(ids, scores, strict=True))
    if len({p[0] for p in pairs}) != len(pairs):
        raise ValueError("exact document references overlap")
    return sorted(pairs, key=lambda pair: (pair[1], pair[0]))[:K]


def result_checks(
    row: dict,
    truth: list[tuple[str, float]],
    allowed: dict[str, dict],
    reference_scores: dict[str, float] | None = None,
) -> dict:
    ids, scores = row["result_ids"], row.get("scores", [])
    complete = not row.get("error") and len(ids) == K and len(set(ids)) == K
    valid_scores = len(scores) == len(ids) and all(finite(s) for s in scores)
    order = bool(complete and valid_scores and all(a <= b + EPSILON for a, b in zip(scores, scores[1:], strict=False)))
    bindings = row.get("result_bindings", [])
    isolated = (
        len(bindings) == len(ids)
        and [item.get("id") for item in bindings] == ids
        and all(
            item.get("id") in allowed
            and all(item.get(key) == allowed[item["id"]][key] for key in ("libraryId", "documentId", "generation"))
            for item in bindings
        )
    )
    reference = {identifier for identifier, _score in truth}
    reference_scores = dict(truth) if reference_scores is None else reference_scores
    comparable = [
        (score, reference_scores[identifier])
        for identifier, score in zip(ids, scores, strict=False)
        if identifier in reference_scores
    ]
    score_mismatches = sum(not finite(score) or abs(score - exact) > EPSILON for score, exact in comparable)
    recall = len(set(ids[:K]) & reference) / K if len(truth) == K else None
    # Tied boundary rows can have equal validity despite different IDs. Count only
    # delivered original IDs whose service distance is tied at the global cutoff.
    tie_recall = recall
    if len(truth) == K and valid_scores:
        tie_matches = sum(
            identifier in reference or abs(score - truth[-1][1]) <= EPSILON
            for identifier, score in zip(ids[:K], scores[:K], strict=True)
        )
        tie_recall = min(1.0, tie_matches / K)
    return {
        "complete": bool(complete),
        "sourceBindings": isolated,
        "distanceOrder": order,
        "recallAt10": recall,
        "tieAwareRecallAt10": tie_recall,
        "distancesCompared": len(comparable),
        "distanceMismatches": score_mismatches,
        "scoresMatchExact": bool(valid_scores and score_mismatches == 0),
        "passed": bool(
            complete
            and isolated
            and order
            and score_mismatches == 0
            and tie_recall is not None
            and tie_recall >= RECALL_FLOOR
        ),
    }


async def census(container, inputs: dict) -> dict:
    library = inputs["libraryId"]
    counts = Counter(row["documentId"] for row in inputs["chunks"])
    documents = {}
    for document, expected in sorted(counts.items()):
        tally = RawTally()
        result = [
            item
            async for item in container.query_items(
                "SELECT VALUE COUNT(1) FROM c WHERE c.libraryId = @libraryId "
                "AND c.documentId = @documentId AND c.generation = @generation",
                parameters=[
                    {"name": "@libraryId", "value": library},
                    {"name": "@documentId", "value": document},
                    {"name": "@generation", "value": 1},
                ],
                partition_key=[library, document],
                raw_response_hook=tally,
            )
        ]
        if result != [expected]:
            raise ValueError("actual scope inventory differs from frozen source")
        ranges = sorted({r["range"] for r in tally.response_metrics if r["range"] is not None})
        documents[document] = {
            "rows": expected,
            "ranges": ranges,
            "rangeComplete": bool(tally.response_metrics)
            and all(r["range"] is not None for r in tally.response_metrics),
        }
    return {
        "documents": documents,
        "knownRanges": sorted({r for doc in documents.values() for r in doc["ranges"]}),
        "rangeComplete": all(doc["rangeComplete"] for doc in documents.values()),
    }


async def measure(args: argparse.Namespace) -> None:
    if read(args.state / "load.json").get("status") != "complete":
        raise ValueError("load must complete before measurement")
    output = args.state / "measurements.jsonl"
    if output.exists():
        raise ValueError("measurement already attempted; preserve partial run and use a new named state")
    credential, cosmos, container, controls, inputs = await clients(args.state, args.canary)
    throttle = Throttle()
    try:
        save(args.state / "status.json", {"status": "running"})
        before = await census(container, inputs)
        save(args.state / "placement-before.json", before)
        readiness = await wait_for_index(
            container, inputs["libraryId"], inputs["queryVectors"][0], len(inputs["chunks"]), throttle, args.minutes
        )
        save(args.state / "readiness.json", readiness)
        documents = sorted(before["documents"])
        with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:

            async def query(kind, ordinal, repeat, document=None):
                hybrid = kind == "hybrid"
                vector = inputs["queryVectors"][ordinal + (QUERIES if hybrid else 0)]
                terms = lexical_terms(inputs["hybridQueries"][ordinal]) if hybrid else []
                sql, params = diagnostic_query(
                    inputs["libraryId"], vector, document=document, terms=terms, exact=kind == "document-exact"
                )
                result = await run_query(
                    container,
                    sql,
                    params,
                    inputs["libraryId"],
                    SearchMeasurement(kind, ordinal),
                    throttle,
                    scored=True,
                    prefix_partition_key=kind not in {"no-prefix", "hybrid"},
                    document_partition=document,
                )
                row = asdict(result) | {"repeat": repeat, "document": document, "querySha256": digest(sql.encode())}
                stream.write(json.dumps(row, allow_nan=False) + "\n")
                stream.flush()

            for ordinal in range(QUERIES):
                for document in documents:
                    await query("document-exact", ordinal, 0, document)
            for repeat in range(REPEATS):
                order = ("prefix", "no-prefix", "hybrid") if repeat % 2 == 0 else ("hybrid", "no-prefix", "prefix")
                for ordinal in range(QUERIES):
                    for kind in order:
                        await query(kind, ordinal, repeat)
        after = await census(container, inputs)
        save(args.state / "placement-after.json", after)
        if before != after:
            raise ValueError("scope placement or inventory changed during the run")
        frozen(args.state)
        save(args.state / "status.json", {"status": "complete", "sourceAndCodeUnchanged": True})
    except BaseException as error:
        save(args.state / "status.json", {"status": "incomplete", "errorCategory": type(error).__name__})
        raise
    finally:
        await cosmos.close()
        await credential.close()
    print("Completed serial direct database probes; no answering model or held-out task used.")


def percentile(values: list[float], fraction: float) -> float | None:
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 2) if values else None


def report(args: argparse.Namespace) -> dict:
    controls, inputs = frozen(args.state)
    rows = [json.loads(line) for line in (args.state / "measurements.jsonl").read_text().splitlines()]
    counts = dict(Counter(row["documentId"] for row in inputs["chunks"]))
    allowed = {row["id"]: row for row in inputs["chunks"]}
    expected = {
        (kind, query, repeat, None)
        for kind in ("prefix", "no-prefix", "hybrid")
        for query in range(QUERIES)
        for repeat in range(REPEATS)
    }
    expected |= {("document-exact", query, 0, document) for query in range(QUERIES) for document in counts}
    observed = [(row["kind"], row["query"], row["repeat"], row["document"]) for row in rows]
    complete = len(observed) == len(expected) and set(observed) == expected
    exact = defaultdict(dict)
    for row in rows:
        if row["kind"] == "document-exact":
            exact[row["query"]][row["document"]] = row
    truth = {}
    errors = []
    for query in range(QUERIES):
        try:
            truth[query] = merged_exact(exact[query], counts, allowed)
        except ValueError:
            errors.append(query)
    before = read(args.state / "placement-before.json")
    after_path = args.state / "placement-after.json"
    unchanged = after_path.exists() and before == read(after_path)
    readiness = read(args.state / "readiness.json")
    results = {}
    for kind in ("prefix", "no-prefix", "hybrid"):
        selected = [row for row in rows if row["kind"] == kind]
        checks = [
            result_checks(
                row,
                truth.get(row["query"], []),
                allowed,
                {
                    identifier: score
                    for doc in exact[row["query"]].values()
                    for identifier, score in zip(doc["result_ids"], doc["scores"], strict=False)
                    if finite(score)
                },
            )
            for row in selected
        ]
        distinct = [
            len({tuple(row["result_ids"]) for row in selected if row["repeat"] == repeat and not row.get("error")})
            for repeat in range(REPEATS)
        ]
        recall = (
            [
                statistics.fmean(
                    check["tieAwareRecallAt10"]
                    for row, check in zip(selected, checks, strict=True)
                    if row["repeat"] == repeat
                )
                if sum(
                    row["repeat"] == repeat and check["tieAwareRecallAt10"] is not None
                    for row, check in zip(selected, checks, strict=True)
                )
                == QUERIES
                else None
                for repeat in range(REPEATS)
            ]
            if kind != "hybrid"
            else None
        )
        valid = (
            complete
            and unchanged
            and not errors
            and all(check["complete"] and check["sourceBindings"] for check in checks)
        )
        correct = (
            valid
            and all(check["distanceOrder"] and check["scoresMatchExact"] for check in checks)
            and all(value is not None and value >= RECALL_FLOOR for value in recall)
            if kind != "hybrid"
            else valid and all(value >= QUERIES // 2 for value in distinct)
        )
        ranges_complete = (
            bool(selected) and all(row["range_diagnostics_complete"] for row in selected) and before["rangeComplete"]
        )
        fanout = (
            any(not set(row["partition_ranges"]).issubset(before["knownRanges"]) for row in selected)
            if ranges_complete
            else None
        )
        charges = [row["known_attempt_charge"] for row in selected]
        latency = [row["milliseconds"] for row in selected]
        results[kind] = {
            "calls": len(selected),
            "correctInMeasuredScope": bool(correct),
            "recallAt10RepeatMeans": recall,
            "distinctRankingsPerRepeat": distinct if kind == "hybrid" else None,
            "sourceBindingFailures": sum(not c["sourceBindings"] for c in checks),
            "cardinalityFailures": sum(not c["complete"] for c in checks),
            "distanceOrderFailures": sum(not c["distanceOrder"] for c in checks) if kind != "hybrid" else None,
            "distancesCompared": sum(c["distancesCompared"] for c in checks) if kind != "hybrid" else None,
            "distanceMismatches": sum(c["distanceMismatches"] for c in checks) if kind != "hybrid" else None,
            "errors": sum(bool(row["error"]) for row in selected),
            "knownAttemptRU": round(sum(charges), 2),
            "totalRU": round(sum(charges), 2)
            if selected and all(row["attempt_charge_complete"] for row in selected)
            else None,
            "ruMedian": statistics.median(charges) if charges else None,
            "latencyP50Ms": percentile(latency, 0.50),
            "latencyP95Ms": percentile(latency, 0.95),
            "applicationRetries": sum(row["throttled"] for row in selected),
            "applicationRetryWaitMs": sum(row["retry_wait_ms"] for row in selected),
            "rangeDiagnosticsComplete": ranges_complete,
            "contactsOutsideVerifiedScope": fanout,
            "indexTypes": sorted(
                {
                    index.get("IndexSpec", {}).get("VectorIndexType", "unknown")
                    for row in selected
                    for report in row["index_utilizations"]
                    if isinstance(report, dict)
                    for index in report.get("UtilizedVectorIndexes", [])
                }
            ),
        }
    winner_ranges = Counter()
    for merged in truth.values():
        if merged:
            document = allowed[merged[0][0]]["documentId"]
            for range_id in before.get("documents", {}).get(document, {}).get("ranges", []):
                winner_ranges[range_id] += 1
    out = {
        "schema": "fli.retrieval-r1-result.v1",
        "baselineCommit": BASE,
        "controls": controls,
        "sources": [
            {key: document[key] for key in ("fileIndex", "sha256", "chunks", "sizeBytes") if key in document}
            for document in inputs["documents"]
        ],
        "vectors": len(inputs["chunks"]),
        "documents": len(counts),
        "measuredCalls": len(rows),
        "expectedCalls": len(expected),
        "complete": complete
        and unchanged
        and not errors
        and read(args.state / "status.json").get("status") == "complete",
        "independentExactQueries": len(truth),
        "exactReferenceFailures": len(errors),
        "sourceAndPlacementUnchanged": unchanged,
        "verifiedLibraryRangeCount": len(before["knownRanges"]) if before["rangeComplete"] else None,
        "multiRangeLibraryProven": bool(before["rangeComplete"] and len(before["knownRanges"]) > 1),
        "queryWinnerRangeCount": len(winner_ranges) if before["rangeComplete"] else None,
        "queryWinnersPerRange": sorted(winner_ranges.values()) if before["rangeComplete"] else None,
        "multiRangeRankingConfirmed": bool(
            before["rangeComplete"] and len(winner_ranges) > 1 and results["prefix"]["correctInMeasuredScope"]
        ),
        "actualScopeReady": readiness.get("engaged", False),
        "readinessSeconds": readiness.get("secondsToEngage"),
        "results": results,
        "limits": [
            "No application API permission/lifecycle gate is replaced by direct chunk diagnostics.",
            "No hybrid semantic relevance or exact-hybrid recall claim.",
            "No performance crossover, layout candidate or representation change evaluated.",
            "Raw-hook known RU excludes unobserved query-plan/SDK-internal requests; unknown total stays null.",
            "Scalar document counts prove placement only where every response supplies a range identifier.",
            "Source-derived canary queries measure database agreement, not workplace task relevance.",
        ],
    }
    save(args.out, out)
    print("Wrote sanitized R1 diagnostic report.")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("prepare", "load", "measure", "report"))
    parser.add_argument("--state", type=private_path, required=True)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--canary", type=Path)
    parser.add_argument("--minutes", type=float, default=20)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    args.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.command == "prepare":
        if not args.capture or not args.canary:
            parser.error("prepare requires --capture and --canary")
        prepare(args)
        return
    if args.command == "report":
        if not args.out:
            parser.error("report requires --out")
        report(args)
        return
    if not args.canary:
        parser.error("load/measure requires --canary")
    with (args.state / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run({"load": load, "measure": measure}[args.command](args))


if __name__ == "__main__":
    main()
