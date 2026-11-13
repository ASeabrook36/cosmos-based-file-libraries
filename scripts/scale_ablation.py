"""Paired E59 shard-key ablation, with private resumable state and numerical drift.

Provisioning and canaries are separate explicit steps. This runner only writes
the two pre-canary-validated disposable databases bound in a private config.
It never deletes databases or changes an account or application setting.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fli.query import STORED_DTYPE, SearchFilters, render_search
from scripts import scale_probe
from scripts.canary import K, RawTally, SearchMeasurement, Throttle, exact_variant, run_query, scored_variant, write_all
from scripts.scale_ablation_stats import (
    compare_arms,
    compare_summaries,
    index_summary,
    summarize_control,
    summarize_probe,
)

ARMS = ("split", "unsplit")
REPO = Path(__file__).resolve().parent.parent


def private_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if (
        path.is_relative_to(REPO)
        or ".git" in path.parts
        or any((parent / ".git").exists() for parent in (path, *path.parents))
    ):
        raise ValueError("configuration and raw state must be outside the repository")
    return path


def read(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def log(message: str) -> None:
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}", flush=True)


class Budget:
    """Checkpoint visible request charges; include canaries and failed attempts."""

    def __init__(self, state: Path, limit: float) -> None:
        self.path = state / "budget.json"
        self.limit = limit
        self.data = read(self.path, {"knownRu": 0.0, "categories": {}})

    def add(self, category: str, amount: float) -> None:
        self.data["knownRu"] += amount
        categories = self.data["categories"]
        categories[category] = categories.get(category, 0.0) + amount
        save(self.path, self.data)

    def check(self) -> None:
        if self.data["knownRu"] >= self.limit:
            raise RuntimeError("request budget reached; resources retained and run paused")


def bind(config: dict, arm: str) -> None:
    os.environ.update({k: str(v) for k, v in config.get("env", {}).items()})
    os.environ["COSMOS_ENDPOINT"] = config["cosmosEndpoint"]
    os.environ["COSMOS_DATABASE"] = config["arms"][arm]["database"]
    os.environ["AZURE_CONFIG_DIR"] = config["azureConfigDir"]


async def query(container: Any, library: str, vector: list, kind: str, q: int, run: int, budget: Budget) -> dict:
    budget.check()
    rendered = render_search(library, vector, [], SearchFilters(), K)
    sql = exact_variant(rendered.sql) if kind == "exact" else rendered.sql
    m = await run_query(
        container, sql, rendered.parameters, library, SearchMeasurement(kind, q), Throttle(), scored=False
    )
    row = asdict(m) | {"library": library, "run": run}
    budget.add(kind, m.known_attempt_charge or m.request_charge)
    return row


async def control(container: Any, vector: list, run: int, budget: Budget) -> dict:
    """E59's isolated id-only global control, with neither filter nor prefix."""
    budget.check()
    tally = RawTally()
    vd = f"VectorDistance(c.embedding, @queryVector, false, {{'dataType': '{STORED_DTYPE}'}})"
    started = time.perf_counter()
    ids, error = [], None
    try:
        ids = [
            row["id"]
            async for row in container.query_items(
                f"SELECT TOP {K} c.id FROM c ORDER BY {vd}",
                parameters=[{"name": "@queryVector", "value": vector}],
                populate_query_metrics=True,
                populate_index_metrics=True,
                raw_response_hook=tally,
            )
        ]
    except Exception as exc:
        error = type(exc).__name__
    row = {
        "kind": "control",
        "query": 0,
        "run": run,
        "request_charge": tally.request_charge,
        "retrieved_document_count": tally.metrics.get("retrievedDocumentCount"),
        "milliseconds": round((time.perf_counter() - started) * 1000, 1),
        "http_responses": tally.responses,
        "result_ids": ids,
        "error": error,
        "index_utilization": tally.index_utilization,
        "index_utilizations": tally.index_utilizations,
        "response_metrics": tally.response_metrics,
    }
    budget.add("control", tally.request_charge)
    return row


async def load_stage(config: dict, state: Path, stage: int, budget: Budget) -> None:
    """Read each stage once, writing identical batches to the two arms in turn."""
    lines, plan = scale_probe.stage_line_range(stage), scale_probe.assignment(stage)
    progress = {
        arm: read(state / arm / f"load-{stage}.json", {"written": 0, "ru": 0.0, "seconds": 0.0}) for arm in ARMS
    }
    clients = {}
    for arm in ARMS:
        bind(config, arm)
        clients[arm] = await scale_probe._clients()
    batch = []
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    async def flush(end: int) -> None:
        for arm in ARMS:
            p = progress[arm]
            if end <= p["written"]:
                continue
            start = end - len(batch)
            if p["written"] < start:
                raise RuntimeError("load checkpoint has a gap")
            pending = batch[max(0, p["written"] - start) :]
            budget.check()
            began = time.perf_counter()
            throttle = Throttle()
            ru = await write_all(clients[arm][2], pending, throttle, lambda _n: None)
            p["written"] = end
            p["ru"] += ru
            p["seconds"] += time.perf_counter() - began
            p["throttled"] = p.get("throttled", 0) + throttle.count
            save(state / arm / f"load-{stage}.json", p)
            budget.add(f"load-{arm}", ru)
        if end % 5000 < scale_probe.BATCH or end == len(lines):
            log(f"stage {stage}: both arms wrote {end}/{len(lines)} rows; known RU {budget.data['knownRu']:.0f}")

    try:
        for number, text, vector in scale_probe.iter_lines(
            Path(config["corpus"]), lines, config["textField"], config["vectorField"]
        ):
            offset = number - lines.start
            library, document, chunk = plan[offset]
            batch.append(scale_probe.chunk_body(library, document, chunk, text, vector, now))
            if len(batch) == scale_probe.BATCH:
                await flush(offset + 1)
                batch.clear()
        if batch:
            await flush(len(lines))
        if any(p["written"] != len(lines) for p in progress.values()):
            raise RuntimeError("corpus ended before the stage was complete")
    finally:
        for credential, cosmos, _container in clients.values():
            await cosmos.close()
            await credential.close()


async def ready(
    config: dict, state: Path, stage: int, vectors: dict, budget: Budget, minutes: float, settle: float
) -> None:
    """Require utilized-index evidence in every probe without requiring cheapness."""
    for arm in ARMS:
        path = state / arm / f"ready-{stage}.json"
        if read(path, {}).get("complete"):
            continue
        bind(config, arm)
        credential, cosmos, container = await scale_probe._clients()
        started, polls, previous = time.monotonic(), [], None
        try:
            ranges = [r async for r in container.read_feed_ranges(force_refresh=True)]
            if len(ranges) != 1:
                raise RuntimeError("experiment no longer has one physical partition range")
            while True:
                current = []
                for library, questions in sorted(vectors.items()):
                    budget.check()
                    rendered = render_search(library, questions[0], [], SearchFilters(), K)
                    m = await run_query(
                        container,
                        scored_variant(rendered.sql),
                        rendered.parameters,
                        library,
                        SearchMeasurement("readiness", 0),
                        Throttle(),
                        scored=True,
                    )
                    budget.add("readiness", m.known_attempt_charge or m.request_charge)
                    row = asdict(m) | {"library": library}
                    current.append(row)
                utilized = index_summary(current)
                # The unsplit treatment is established by stored-policy readback.
                # Do not reject an unexpected service-reported implicit scope.
                correct = utilized["indexReportsComplete"] and (
                    arm == "unsplit" or all(i["shardKeys"] == ["/libraryId"] for i in utilized["vectorIndexes"])
                )
                valid = all(not r["error"] and not r["null_scores"] and len(set(r["result_ids"])) == K for r in current)
                stable = previous is not None and all(
                    abs(a["request_charge"] - b["request_charge"]) <= max(0.1, b["request_charge"] * 0.1)
                    and index_summary([a])["vectorIndexes"] == index_summary([b])["vectorIndexes"]
                    for a, b in zip(current, previous, strict=True)
                )
                polls.append({"elapsedSeconds": time.monotonic() - started, "rows": current})
                complete = bool(correct and valid and stable)
                checkpoint = {"complete": False, "indexReady": complete, "physicalRanges": len(ranges), "polls": polls}
                save(path, checkpoint)
                if complete:
                    log(f"stage {stage} {arm}: utilized index stable in all ten probes; settling {settle:g}s")
                    await asyncio.sleep(settle)
                    save(path, checkpoint | {"complete": True, "settledSeconds": settle})
                    break
                if time.monotonic() - started >= minutes * 60:
                    raise RuntimeError(f"stage {stage} {arm}: bounded index readiness wait failed")
                previous = current
                await asyncio.sleep(30)
        finally:
            await cosmos.close()
            await credential.close()


async def inventory(config: dict, state: Path, stage: int, budget: Budget) -> None:
    """Verify actual counts and membership, including canary cleanup and lag data."""
    libraries = [lib for s in range(1, stage + 1) for lib in scale_probe.stage_libraries(s)]
    if stage == 3:
        libraries += scale_probe.stage_libraries(4)
    expected_ids = {lib.library_id for lib in libraries}
    expected_count = sum(lib.size for lib in libraries)
    for arm in ARMS:
        bind(config, arm)
        credential, cosmos, container = await scale_probe._clients()
        try:
            budget.check()
            tally = RawTally()
            counts = [
                count async for count in container.query_items("SELECT VALUE COUNT(1) FROM c", raw_response_hook=tally)
            ]
            budget.add("inventory", tally.request_charge)
            budget.check()
            tally = RawTally()
            observed_ids = {
                library
                async for library in container.query_items(
                    "SELECT DISTINCT VALUE c.libraryId FROM c ORDER BY c.libraryId", raw_response_hook=tally
                )
            }
            budget.add("inventory", tally.request_charge)
            valid = counts == [expected_count] and observed_ids == expected_ids
            save(
                state / arm / f"inventory-{stage}.json",
                {
                    "vectors": counts[0] if len(counts) == 1 else None,
                    "libraries": len(observed_ids),
                    "matchesExpected": valid,
                },
            )
            if not valid:
                raise RuntimeError(f"stage {stage} {arm}: actual inventory differs from the frozen layout")
        finally:
            await cosmos.close()
            await credential.close()


async def measure(config: dict, state: Path, stage: int, vectors: dict, budget: Budget) -> None:
    clients, data = {}, {}
    for arm in ARMS:
        bind(config, arm)
        clients[arm] = await scale_probe._clients()
        data[arm] = read(state / arm / f"measure-{stage}.json", {"rows": [], "controls": []})
    try:
        for run in range(scale_probe.RUNS):
            for lib_i, (library, questions) in enumerate(sorted(vectors.items())):
                for q, vector in enumerate(questions):
                    order = ARMS if (run + q + lib_i) % 2 == 0 else ARMS[::-1]
                    for arm in order:
                        kinds = ("vector", "exact") if run == 0 else ("vector",)
                        for kind in kinds:
                            key = (library, q, run, kind)
                            if any((r["library"], r["query"], r["run"], r["kind"]) == key for r in data[arm]["rows"]):
                                continue
                            budget.check()
                            row = await query(clients[arm][2], library, vector, kind, q, run, budget)
                            data[arm]["rows"].append(row)
                            save(state / arm / f"measure-{stage}.json", data[arm])
                            if row["error"]:
                                raise RuntimeError(f"stage {stage} {arm}: query error recorded; run stopped")
                log(f"stage {stage} repeat {run + 1}: both arms completed probe {lib_i + 1}/10")
            for arm in ARMS if run % 2 == 0 else ARMS[::-1]:
                if not any(r["run"] == run for r in data[arm]["controls"]):
                    data[arm]["controls"].append(await control(clients[arm][2], vectors["scale-p01"][0], run, budget))
                    save(state / arm / f"measure-{stage}.json", data[arm])
            log(f"stage {stage}: repeat {run + 1}/3 done; known RU {budget.data['knownRu']:.0f}")
    finally:
        for credential, cosmos, _container in clients.values():
            await cosmos.close()
            await credential.close()


def report(state: Path, output: Path) -> dict:
    stages, comparisons, drift, control_drift = {}, {}, {}, {}
    for stage in (1, 2, 3):
        s = stages[str(stage)] = {}
        for arm in ARMS:
            raw = read(state / arm / f"measure-{stage}.json", {"rows": [], "controls": []})
            s[arm] = {
                "probes": {
                    lib.library_id: summarize_probe([r for r in raw["rows"] if r["library"] == lib.library_id])
                    for lib in scale_probe.stage_libraries(1)
                },
                "control": summarize_control(raw["controls"]),
                "load": {
                    k: v
                    for k, v in read(state / arm / f"load-{stage}.json", {}).items()
                    if k in {"written", "ru", "seconds", "throttled"}
                },
                "readinessComplete": read(state / arm / f"ready-{stage}.json", {}).get("complete", False),
                "observedInventory": {
                    k: v
                    for k, v in read(state / arm / f"inventory-{stage}.json", {}).items()
                    if k in {"vectors", "libraries", "matchesExpected"}
                },
            }
        comparisons[str(stage)] = {
            lib: compare_arms(s["split"]["probes"][lib], s["unsplit"]["probes"][lib]) for lib in s["split"]["probes"]
        }
        if stage > 1:
            drift[str(stage)] = {
                arm: {
                    lib: compare_summaries(stages["1"][arm]["probes"][lib], s[arm]["probes"][lib])
                    for lib in s[arm]["probes"]
                }
                for arm in ARMS
            }
            control_drift[str(stage)] = {
                arm: {
                    key: value
                    for key, value in compare_summaries(stages["1"][arm]["control"], s[arm]["control"]).items()
                    if key.endswith(("Delta", "DriftPct"))
                }
                for arm in ARMS
            }
    result = {
        "kind": "paired-E59-vector-shard-ablation",
        "createdAt": datetime.now(UTC).isoformat(),
        "method": {
            "seed": scale_probe.SEED,
            "runs": 3,
            "questionsPerLibrary": 20,
            "k": K,
            "query": "unmodified render_search SQL; partition_key_for library prefix; metrics enabled",
            "control": "fixed E59 question, id-only TOP 10, no library filter or partition prefix, three repeats",
            "latency": "client elapsed milliseconds including visible retry waits",
            "recall": "every ANN repeat against one exact reference per question/stage/arm",
            "historicalQuestionVectorsAvailable": False,
            "tolerance": 0.20,
            "historicalComparison": (
                "E59 preserved as recorded; reconstructed questions newly embedded once for both fresh arms"
            ),
        },
        "inventory": {
            "1": {"libraries": 10, "vectors": 25000},
            "2": {"libraries": 413, "vectors": 176599},
            "3": {"libraries": 1014, "vectors": 244773},
        },
        "inputs": read(state / "shared" / "input-hashes.json"),
        "stages": stages,
        "stageDrift": drift,
        "controlStageDrift": control_drift,
        "unsplitVersusSplit": comparisons,
        "knownRequestCost": {
            "knownRu": read(state / "budget.json", {}).get("knownRu"),
            "complete": False,
            "limits": "Visible request charges; canary CRUD/purge and SDK-hidden charges are not fully counted",
        },
        "client": {
            k: v
            for k, v in read(state / "run-metadata.json", {}).get("client", {}).items()
            if k in {"python", "azureCosmos"}
        },
        "embeddingModel": {
            k: v
            for k, v in read(state / "embedding-model-before.json", {}).items()
            if k in {"model", "version", "format", "dimensions"}
        },
        "queryEmbeddingUsage": read(state / "shared" / "embedding-usage.json"),
        "sourceSnapshotSha256": read(state / "run-metadata.json", {}).get("sourceSnapshotSha256"),
        "canaries": {},
    }
    for arm in ARMS:
        canary = read(state / arm / "canary-summary.json", {})
        result["canaries"][arm] = {
            "passed": canary.get("passed"),
            "cleanedUp": canary.get("cleanedUp"),
            "checks": [{key: item.get(key) for key in ("name", "passed", "rule")} for item in canary.get("checks", [])],
            "summary": canary.get("summary"),
            "recallAt10": next(
                (
                    {key: item.get("observed", {}).get(key) for key in ("mean", "min", "queries")}
                    for item in canary.get("checks", [])
                    if item.get("name") == "vector_recall_at_10"
                ),
                None,
            ),
        }
    # Only explicitly named content-free input hashes enter the public receipt.
    if result["inputs"]:
        result["inputs"] = {
            k: v
            for k, v in result["inputs"].items()
            if k
            in {
                "seed",
                "candidateSourceCounts",
                "selectedSourceCounts",
                "questionSourcesSha256",
                "orderedCorpusPrefixRows",
                "orderedCorpusPrefixBytes",
                "orderedCorpusPrefixSha256",
                "questionVectorsSha256",
            }
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


async def run(args: argparse.Namespace) -> None:
    state = private_path(args.state)
    config = read(private_path(args.config))
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    budget = Budget(state, config.get("budgetRu", 80_000_000))
    gates = read(state / "canary-gates.json", {})
    binding = config_digest(config)
    if gates.get("configSha256") != binding:
        raise RuntimeError("canary gates belong to a different private configuration")
    if not all(gates.get(arm, {}).get("passed") for arm in ARMS):
        raise RuntimeError("both arm canaries must pass before scale loading")
    bind(config, "split")
    if not (state / "shared" / "question-vectors.json").exists():
        from fli.embedding import measure_embeddings

        with measure_embeddings() as usage:
            vectors = await scale_probe.embed_questions(argparse.Namespace(state=state / "shared"))
        save(state / "shared" / "embedding-usage.json", asdict(usage))
    else:
        vectors = await scale_probe.embed_questions(argparse.Namespace(state=state / "shared"))
    inputs_path = state / "shared" / "input-hashes.json"
    inputs = read(inputs_path, {})
    vector_hash = hashlib.sha256((state / "shared" / "question-vectors.json").read_bytes()).hexdigest()
    if inputs.get("questionVectorsSha256") not in (None, vector_hash):
        raise RuntimeError("frozen query vectors changed")
    inputs["questionVectorsSha256"] = vector_hash
    save(inputs_path, inputs)
    binding_path = state / "measurement-binding.json"
    measurement_binding = {"configSha256": binding, "inputHashes": inputs}
    if binding_path.exists() and read(binding_path) != measurement_binding:
        raise RuntimeError("measurement state belongs to different configuration or inputs")
    save(binding_path, measurement_binding)
    for stage in (1, 2, 3):
        await load_stage(config, state, stage, budget)
        await inventory(config, state, stage, budget)
        await ready(config, state, stage, vectors, budget, args.minutes, args.settle)
        await measure(config, state, stage, vectors, budget)
        report(state, state / "progress-report.json")
        save(state / "status.json", {"stageCompleted": stage, "complete": stage == 3})
        log(f"stage {stage}: paired measurement complete")
        if stage == 2:
            await load_stage(config, state, 4, budget)
    log("all measurements complete; both disposable databases retained")


def config_digest(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "report"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--settle", type=float, default=300)
    args = parser.parse_args()
    if args.action == "report":
        report(private_path(args.state), args.out)
    elif args.config is None:
        parser.error("run requires --config")
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
