"""Phase 4 scale probe: does a small library's vector search stay scoped as the container fills?

Ten probe libraries of 1,000–5,000 real vectors are measured alone, then again after hundreds
of other libraries share the container (`capacity/untested-at-scale.md` §6, `build-plan.md`
Phase 4). A search is **scoped** when its cost, the documents it reads and its recall do not
change as other libraries' content grows, and Cosmos reports the `/libraryId` vector shard.

Run against a disposable database holding the three `cosmos/*.json` containers
(`infra/scale-lab.bicep`), after the canary has passed on it (CLAUDE.md rule 11)::

    export COSMOS_DATABASE=fli-scale
    python scripts/scale_probe.py load    --corpus FILE --state DIR --stage 1
    python scripts/scale_probe.py wait    --state DIR --stage 1
    python scripts/scale_probe.py measure --state DIR --stage 1
    python scripts/scale_probe.py load    --corpus FILE --state DIR --stage 2   # then wait, measure
    python scripts/scale_probe.py lag     --corpus FILE --state DIR             # N12, after stage 2
    python scripts/scale_probe.py report  --state DIR --out records/scale-probe-<date>.json

The corpus is JSON lines, one passage per line, each holding real text and its real
3,072-dimension `text-embedding-3-large` vector (E14: never random vectors or filler). Library
membership is assigned with a fixed seed. Only `chunks` is written: this measures the
container's query, not ingestion, so no library or document records are needed.

``--state`` must be outside the repository: it holds question vectors and result ids. The
report holds metrics only. It never names the corpus file, an account or a passage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fli.query import STORED_DTYPE, SearchFilters, check_query, render_search, to_float16_query  # noqa: E402
from scripts.canary import (  # noqa: E402
    EMBEDDING_MODEL,
    RECALL_FLOOR,
    K,
    RawTally,
    SearchMeasurement,
    Throttle,
    TokenCache,
    chunk_id,
    exact_variant,
    query_sentence,
    recall_at_k,
    run_query,
    scored_variant,
    wait_for_index,
    write_all,
)

SEED = 20261029
CHUNKS_PER_DOCUMENT = 10
QUESTIONS_PER_LIBRARY = 20
RUNS = 3
TOLERANCE = 0.20  # a scoped search changes cost and documents read by at most this fraction
PROBE_SIZES = (1000, 1000, 1500, 1500, 2000, 2500, 3000, 3500, 4000, 5000)
NEW_LIBRARY_SIZE = 1500
BATCH = 200


@dataclass(frozen=True)
class Library:
    library_id: str
    stage: int
    size: int
    role: str  # probe | background | shared | new


def stage_libraries(stage: int, seed: int = SEED) -> list[Library]:
    """The libraries each stage adds. Deterministic, so a resumed run writes the same layout."""
    rng = random.Random(seed * 10 + stage)
    if stage == 1:
        return [Library(f"scale-p{i:02d}", 1, n, "probe") for i, n in enumerate(PROBE_SIZES, 1)]
    if stage == 2:
        small = [Library(f"scale-b{i:04d}", 2, rng.randint(50, 500), "background") for i in range(1, 401)]
        shared = [Library(f"scale-s{i}", 2, n, "shared") for i, n in enumerate((10_000, 15_000, 20_000), 1)]
        return small + shared
    if stage == 3:
        return [Library(f"scale-c{i:04d}", 3, rng.randint(30, 200), "background") for i in range(1, 601)]
    if stage == 4:  # the new library whose index lag is timed (N12)
        return [Library("scale-new", 4, NEW_LIBRARY_SIZE, "new")]
    raise ValueError(f"no stage {stage}")


def stage_line_range(stage: int) -> range:
    """Each stage reads its own contiguous lines of the corpus, after the previous stage's."""
    start = 0
    for s in range(1, stage):
        start += sum(lib.size for lib in stage_libraries(s))
    return range(start, start + sum(lib.size for lib in stage_libraries(stage)))


def assignment(stage: int, seed: int = SEED) -> list[tuple[str, str, int]]:
    """For each line of the stage, in file order: (libraryId, documentId, chunkIndex).

    Lines are shuffled before they are dealt to libraries, so a library is not a run of
    neighbouring passages. Each library's passages are grouped into documents of ten.
    """
    libraries = stage_libraries(stage, seed)
    slots = [lib.library_id for lib in libraries for _ in range(lib.size)]
    random.Random(seed * 100 + stage).shuffle(slots)
    seen: dict[str, int] = {}
    out = []
    for library_id in slots:
        n = seen.get(library_id, 0)
        seen[library_id] = n + 1
        out.append((library_id, f"{library_id}-d{n // CHUNKS_PER_DOCUMENT:04d}", n % CHUNKS_PER_DOCUMENT))
    return out


def chunk_body(library_id: str, document_id: str, chunk_index: int, text: str, vector: list[float], now: str) -> dict:
    return {
        "id": chunk_id(document_id, 1, chunk_index),
        "libraryId": library_id,
        "documentId": document_id,
        "generation": 1,
        "chunkIndex": chunk_index,
        "contentKind": "text",
        "text": text,
        "heading": None,
        "location": {},
        "imageRef": None,
        "imageMeta": None,
        "fileName": f"{document_id}.txt",
        "fileType": "txt",
        "uploadedAt": now,
        "authoredAt": None,
        "author": None,
        "periodStart": None,
        "periodEnd": None,
        "facets": [],
        "embedding": vector,
        "embeddingModel": EMBEDDING_MODEL,
        "chunkingVersion": "scale-probe",
        "createdAt": now,
    }


def iter_lines(path: Path, lines: range, text_field: str, vector_field: str) -> Iterator[tuple[int, str, list]]:
    """Stream only the wanted lines; the file may be far larger than memory."""
    with path.open("rb") as handle:
        for number, raw in enumerate(handle):
            if number < lines.start:
                continue
            if number >= lines.stop:
                return
            row = json.loads(raw)
            text, vector = row.get(text_field), row.get(vector_field)
            if not isinstance(text, str) or not isinstance(vector, list) or len(vector) != 3072:
                raise SystemExit(f"line {number} lacks real text or a 3,072-dimension vector")
            yield number, text, vector


def vector_index(report: Any) -> tuple[str | None, list[str]]:
    """The vector index Cosmos says it used, and that index's shard keys."""
    if not isinstance(report, dict):
        return None, []
    for entry in report.get("UtilizedVectorIndexes") or []:
        spec = entry.get("IndexSpec") or {}
        return spec.get("VectorIndexType"), list(spec.get("ShardKeys") or [])
    return None, []


def _median(values: Sequence[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One probe library at one stage: per-question medians over runs, then medians over questions."""
    normal = [r for r in rows if r["kind"] == "vector" and not r["error"]]
    exact = {r["query"]: r for r in rows if r["kind"] == "exact" and not r["error"]}
    by_query: dict[int, list[dict]] = {}
    for r in normal:
        by_query.setdefault(r["query"], []).append(r)
    run_medians = [_median([r["request_charge"] for r in normal if r["run"] == run]) for run in range(RUNS)]
    recalls = [
        recall_at_k(runs[0]["result_ids"], exact[q]["result_ids"], K) for q, runs in by_query.items() if q in exact
    ]
    indexes = sorted({f"{t}:{','.join(k)}" for r in normal for t, k in [vector_index(r["index_utilization"])] if t})
    return {
        "searches": len(normal),
        "errors": sum(1 for r in rows if r["error"]),
        "nullScores": sum(r["null_scores"] for r in normal),
        "throttled": sum(r["throttled"] for r in rows),
        "ruMedian": _median([_median([r["request_charge"] for r in runs]) for runs in by_query.values()]),
        "ruRunMedians": run_medians,
        "retrievedMedian": _median(
            [_median([r["retrieved_document_count"] for r in runs]) for runs in by_query.values()]
        ),
        "msMedian": _median([_median([r["milliseconds"] for r in runs]) for runs in by_query.values()]),
        "exactRuMedian": _median([r["request_charge"] for r in exact.values()]),
        "recallAt10Mean": round(statistics.fmean(recalls), 4) if recalls else None,
        "vectorIndexes": indexes,
    }


def grade(baseline: dict[str, Any], later: dict[str, Any], tolerance: float = TOLERANCE) -> dict[str, Any]:
    """Scoped when cost and documents read hold within ``tolerance`` and the run-to-run spread,
    Cosmos reports the library shard, and recall stays at the floor or above."""

    def holds(key: str, spread_key: str | None = None) -> bool:
        before, after = baseline.get(key), later.get(key)
        if before is None or after is None:
            return False
        limit = before * tolerance
        if spread_key:
            runs = [v for v in (baseline.get(spread_key) or []) if v is not None]
            if runs:
                limit = max(limit, max(runs) - min(runs))
        return abs(after - before) <= limit

    shard = bool(later["vectorIndexes"]) and all(i.endswith("/libraryId") for i in later["vectorIndexes"])
    recall = later["recallAt10Mean"] is not None and later["recallAt10Mean"] >= RECALL_FLOOR
    checks = {
        "ruHolds": holds("ruMedian", "ruRunMedians"),
        "retrievedHolds": holds("retrievedMedian"),
        "libraryShardReported": shard,
        "recallAtFloor": recall,
    }
    return checks | {"scoped": all(checks.values())}


# ---------------------------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------------------------


def log(message: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {message}", flush=True)


def _state(args: argparse.Namespace, name: str) -> Path:
    return args.state / name


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def _write(path: Path, value: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


async def _clients():
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=credential)
    database = cosmos.get_database_client(os.environ.get("COSMOS_DATABASE") or "fli-scale")
    return credential, cosmos, database.get_container_client("chunks")


async def load(args: argparse.Namespace) -> None:
    """Write one stage's passages. Resumable: ids are deterministic and progress is saved."""
    lines = stage_line_range(args.stage)
    plan = assignment(args.stage)
    progress_path = _state(args, f"load-{args.stage}.json")
    progress = _read(progress_path, {"written": 0, "ru": 0.0, "throttled": 0, "seconds": 0.0})
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    want_questions = args.stage == 1 and not _state(args, "question-sources.json").exists()
    sources: dict[str, list[str]] = {}
    credential, cosmos, container = await _clients()
    throttle = Throttle()
    try:
        batch: list[dict] = []
        started = time.monotonic()

        async def flush() -> None:
            nonlocal batch
            if batch:
                progress["ru"] += await write_all(container, batch, throttle, lambda _n: None)
                progress["written"] += len(batch)
                progress["throttled"] = throttle.count
                progress["seconds"] += time.monotonic() - started
                _write(progress_path, progress)
                batch = []

        skip = progress["written"]
        for number, text, vector in iter_lines(args.corpus, lines, args.text_field, args.vector_field):
            library_id, document_id, chunk_index = plan[number - lines.start]
            if want_questions:
                sentence = query_sentence(text, None)
                if sentence:
                    sources.setdefault(library_id, []).append(sentence)
            if number - lines.start < skip:
                continue
            batch.append(chunk_body(library_id, document_id, chunk_index, text, vector, now))
            if len(batch) >= BATCH:
                started = time.monotonic()
                await flush()
                if progress["written"] % 5000 < BATCH:
                    log(f"stage {args.stage}: {progress['written']}/{len(lines)} written, {throttle.count} retries")
        started = time.monotonic()
        await flush()
    finally:
        await cosmos.close()
        await credential.close()
    if want_questions:  # sentences stay in --state; only their vectors are used later
        rng = random.Random(SEED)
        picked = {lib: rng.sample(s, QUESTIONS_PER_LIBRARY) for lib, s in sorted(sources.items())}
        _write(_state(args, "question-sources.json"), picked)
    log(f"stage {args.stage} loaded: {progress['written']} passages, {progress['ru']:.0f} RU")


async def embed_questions(args: argparse.Namespace) -> dict[str, list[list[float]]]:
    path = _state(args, "question-vectors.json")
    if path.exists():
        return json.loads(path.read_text())
    import httpx
    from azure.identity.aio import DefaultAzureCredential

    from fli.embedding import AzureOpenAIEmbedder

    sources = json.loads(_state(args, "question-sources.json").read_text())
    credential = DefaultAzureCredential()
    try:
        async with httpx.AsyncClient() as http:
            embedder = AzureOpenAIEmbedder(
                endpoint=os.environ["AOAI_ENDPOINT"],
                deployment=os.environ["AOAI_EMBEDDING_DEPLOYMENT"],
                token_provider=TokenCache(credential),
                http=http,
                dimensions=3072,
            )
            vectors = {lib: [to_float16_query(v) for v in await embedder.embed(qs)] for lib, qs in sources.items()}
    finally:
        await credential.close()
    _write(path, vectors)
    return vectors


async def wait(args: argparse.Namespace) -> None:
    """Wait until the stage's largest library stops scanning, then a settle period."""
    library = max(stage_libraries(args.stage), key=lambda lib: lib.size)
    vectors = await embed_questions(args) if args.stage == 1 else None
    probe = (vectors or json.loads(_state(args, "question-vectors.json").read_text()))["scale-p01"][0]
    credential, cosmos, container = await _clients()
    try:
        result = await wait_for_index(container, library.library_id, probe, library.size, Throttle(), args.minutes)
    finally:
        await cosmos.close()
        await credential.close()
    waits = _read(_state(args, "waits.json"), {})
    waits[str(args.stage)] = {k: v for k, v in result.items() if not isinstance(v, (list, dict))}
    _write(_state(args, "waits.json"), waits)
    log(f"index wait for {library.library_id}: {waits[str(args.stage)]}; settling {args.settle} s")
    await asyncio.sleep(args.settle)


async def measure(args: argparse.Namespace) -> None:
    vectors = await embed_questions(args)
    credential, cosmos, container = await _clients()
    throttle = Throttle()
    rows: list[dict] = []
    try:
        for run in range(RUNS):
            for library_id, questions in sorted(vectors.items()):
                for q, vector in enumerate(questions):
                    query = render_search(library_id, vector, [], SearchFilters(), K)
                    check_query(query.sql)
                    kinds = [("vector", scored_variant(query.sql))]
                    if run == 0:
                        kinds.append(("exact", exact_variant(scored_variant(query.sql))))
                    for kind, sql in kinds:
                        m = await run_query(
                            container,
                            sql,
                            query.parameters,
                            library_id,
                            SearchMeasurement(kind, q),
                            throttle,
                            scored=True,
                        )
                        rows.append(asdict(m) | {"library": library_id, "run": run})
            log(f"stage {args.stage}: run {run + 1} of {RUNS} done")
        control = await unscoped_control(container, vectors["scale-p01"][0])
    finally:
        await cosmos.close()
        await credential.close()
    _write(_state(args, f"measure-{args.stage}.json"), {"rows": rows, "control": control})
    summary = {lib: summarize([r for r in rows if r["library"] == lib]) for lib in sorted(vectors)}
    for lib, s in summary.items():
        log(f"{lib}: {s['ruMedian']} RU, {s['retrievedMedian']} read, recall {s['recallAt10Mean']}")
        log(f"{lib}: vector index {s['vectorIndexes']}")
    log(f"unscoped control: {control}")


async def unscoped_control(container: Any, vector: list[float]) -> dict[str, Any]:
    """What a search missing its library filter costs at this container size (E5).

    Deliberately breaks CLAUDE.md rule 2, once per stage, in the disposable database only.
    """
    tally = RawTally()
    vd = f"VectorDistance(c.embedding, @queryVector, false, {{'dataType': '{STORED_DTYPE}'}})"
    sql = f"SELECT TOP {K} c.id FROM c ORDER BY {vd}"
    started = time.perf_counter()
    items = [
        i
        async for i in container.query_items(
            sql,
            parameters=[{"name": "@queryVector", "value": vector}],
            populate_query_metrics=True,
            populate_index_metrics=True,
            raw_response_hook=tally,
        )
    ]
    index_type, shards = vector_index(tally.index_utilization)
    return {
        "ru": round(tally.request_charge, 2),
        "responses": tally.responses,
        "retrieved": tally.metrics.get("retrievedDocumentCount"),
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "rows": len(items),
        "vectorIndex": index_type,
        "shardKeys": shards,
    }


async def lag(args: argparse.Namespace) -> None:
    """N12: how long a new library in a populated container scans before its index engages."""
    library = stage_libraries(4)[0]
    lines = stage_line_range(4)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    bodies = [
        chunk_body(library.library_id, f"{library.library_id}-d{i // 10:04d}", i % 10, text, vector, now)
        for i, (_n, text, vector) in enumerate(iter_lines(args.corpus, lines, args.text_field, args.vector_field))
    ]
    probe = json.loads(_state(args, "question-vectors.json").read_text())["scale-p01"][0]
    credential, cosmos, container = await _clients()
    try:
        started = time.monotonic()
        await write_all(container, bodies, Throttle(), lambda _n: None)
        written = time.monotonic() - started
        result = await wait_for_index(container, library.library_id, probe, len(bodies), Throttle(), args.minutes)
    finally:
        await cosmos.close()
        await credential.close()
    record = {"writeSeconds": round(written, 1)} | {k: v for k, v in result.items() if not isinstance(v, (list, dict))}
    _write(_state(args, "lag.json"), record)
    log(f"new-library index lag: {record}")


def report(args: argparse.Namespace) -> None:
    from azure.cosmos import __version__ as cosmos_version

    stages = sorted(int(p.stem.split("-")[1]) for p in args.state.glob("measure-*.json"))
    summaries = {}
    for stage in stages:
        rows = _read(_state(args, f"measure-{stage}.json"), {})["rows"]
        libraries = sorted({r["library"] for r in rows})
        summaries[stage] = {lib: summarize([r for r in rows if r["library"] == lib]) for lib in libraries}
    sizes = {lib.library_id: lib.size for lib in stage_libraries(1)}
    container = {}
    for stage in stages:
        libs = [lib for s in range(1, stage + 1) for lib in stage_libraries(s)]
        container[stage] = {"libraries": len(libs), "passages": sum(lib.size for lib in libs)}
    grades = {
        stage: {lib: grade(summaries[stages[0]][lib], s) for lib, s in summaries[stage].items()} for stage in stages[1:]
    }
    out = {
        "kind": "phase-4-scale-probe",
        "evidence": "docs/evidence.md E59",
        "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "client": {"azureCosmos": cosmos_version},
        "account": "serverless demo account, disposable database with the cosmos/*.json containers",
        "corpus": "public DBpedia-entity abstracts with publisher-supplied text-embedding-3-large 3,072-d vectors; "
        "one abstract per passage; library membership assigned with a fixed seed",
        "method": {
            "seed": SEED,
            "questionsPerLibrary": QUESTIONS_PER_LIBRARY,
            "questionSource": "a sentence from a passage of the same library, embedded fresh",
            "runs": RUNS,
            "k": K,
            "tolerance": TOLERANCE,
            "recallFloor": RECALL_FLOOR,
            "query": "render_search + scored_variant, routed by partition_key_for (library prefix)",
            "exact": "VectorDistance(..., true, ...), once per question per stage",
        },
        "probeSizes": sizes,
        "container": container,
        "loads": {s: _read(_state(args, f"load-{s}.json"), None) for s in range(1, 5)},
        "indexWaits": _read(_state(args, "waits.json"), {}),
        "stages": summaries,
        "unscopedControl": {s: _read(_state(args, f"measure-{s}.json"), {}).get("control") for s in stages},
        "grades": grades,
        "scopedEverywhere": {s: all(g["scoped"] for g in grades[s].values()) for s in grades},
        "newLibraryIndexLag": _read(_state(args, "lag.json"), None),
    }
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    log(f"wrote {args.out}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("load", "wait", "measure", "lag", "report"))
    parser.add_argument("--state", type=Path, required=True, help="working directory outside the repository")
    parser.add_argument("--corpus", type=Path, help="JSON lines of real text and real vectors")
    parser.add_argument("--stage", type=int, choices=(1, 2, 3))
    parser.add_argument("--text-field", default="searchText")
    parser.add_argument("--vector-field", default="embedding")
    parser.add_argument("--minutes", type=float, default=30, help="index wait limit")
    parser.add_argument("--settle", type=float, default=300, help="seconds to wait after the index engages")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if REPO in args.state.resolve().parents or args.state.resolve() == REPO:
        parser.error("--state must be outside the repository")
    args.state.mkdir(parents=True, exist_ok=True)
    needs = {
        "load": ("corpus", "stage"),
        "wait": ("stage",),
        "measure": ("stage",),
        "lag": ("corpus",),
        "report": ("out",),
    }
    for name in needs[args.command]:
        if getattr(args, name) is None:
            parser.error(f"{args.command} needs --{name}")
    if args.command == "report":
        report(args)
        return 0
    command = {"load": load, "wait": wait, "measure": measure, "lag": lag}[args.command]
    asyncio.run(command(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
