"""Reproducible, isolated Cosmos structure experiment. See docs/structure-lab.md.

All runtime artifacts (source, summaries, vectors, labels, caches and receipts)
must live outside the repository. Console output contains counts and timings only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from fli.chunking import TiktokenTokenizer  # noqa: E402
from fli.embedding import AzureOpenAIEmbedder  # noqa: E402
from fli.proposals import AzureOpenAIChat  # noqa: E402
from fli.structure_lab import (  # noqa: E402
    CONTAINERS,
    VERSION,
    Summarizer,
    digest,
    markdown_document,
    prepare,
    render_query,
    rows_for,
    source_read,
    target_container,
)
from scripts.canary import (  # noqa: E402
    SearchMeasurement,
    Throttle,
    TokenCache,
    definition_mismatches,
    recall_at_k,
    run_query,
    target_fingerprint,
    write_all,
)


def private_directory(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(REPO):
        raise ValueError("runtime artifacts must be outside the repository")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save(path: Path, value: object) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


class Cache(dict):
    """Checkpoint after each model call or embedding batch, so retries reuse completed work."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(json.loads(path.read_text()) if path.exists() else {})

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        save(self.path, self)

    def put_batch(self, values: dict) -> None:
        self.update(values)
        save(self.path, self)


def load_manifest(path: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads(path.read_text())
    documents = []
    for item in manifest["documents"]:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", item["id"]):
            raise ValueError("document ids must be short opaque slugs")
        source_path = path.parent / item["path"]
        if source_path.suffix.lower() not in (".md", ".txt"):
            raise ValueError("the first adapter accepts Markdown or plain text only")
        # Preserve CRLF and exact Unicode source text for subtree reads.
        source = source_path.read_bytes().decode("utf-8")
        documents.append(markdown_document(item["id"], item["title"], source, item.get("genre", "unknown")))
    queries = manifest.get("queries", [])
    if len({q["id"] for q in queries}) != len(queries):
        raise ValueError("query ids must be unique")
    for q in queries:
        render_query("validation", [0.1], q["level"], q.get("top", 10), document_id=q.get("documentId"))
        if not isinstance(q["text"], str) or not q["text"].strip():
            raise ValueError("each query needs text")
    return manifest, documents


async def cached_vectors(texts: list[str], embedder, cache: Cache, identity: str) -> list[list[float]]:
    keys = [digest([VERSION, identity, text]) for text in texts]
    missing = {key: text for key, text in zip(keys, texts, strict=True) if key not in cache}
    pairs = list(missing.items())
    for start in range(0, len(pairs), 16):
        batch = pairs[start : start + 16]
        vectors = await embedder.embed([text for _, text in batch])
        completed = {}
        for (key, _), vector in zip(batch, vectors, strict=True):
            if len(vector) != 3072 or not all(math.isfinite(v) for v in vector):
                raise ValueError("embedding does not match the lab vector policy")
            completed[key] = vector
        cache.put_batch(completed)
    return [cache[key] for key in keys]


async def freeze(args, directory: Path) -> None:
    import httpx
    from azure.identity.aio import DefaultAzureCredential

    manifest, documents = load_manifest(args.manifest)
    tokenizer = TiktokenTokenizer()  # no fallback tokenizer in live artifacts
    credential = DefaultAzureCredential()
    started = perf_counter()
    try:
        async with httpx.AsyncClient() as http:
            token = TokenCache(credential)
            chat = AzureOpenAIChat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"], token, http)
            summary = Summarizer(
                chat,
                tokenizer,
                chat.cache_identity + args.model_revision,
                cache=Cache(directory / "summary-cache.json"),
            )
            bundle = await prepare(
                documents, summary, "structure-lab-" + uuid.uuid4().hex, manifest.get("ownerDescription", "")
            )
            rows = rows_for(bundle, tokenizer)
            queries = manifest.get("queries", [])
            embedder = AzureOpenAIEmbedder(
                os.environ["AOAI_ENDPOINT"], os.environ["AOAI_EMBEDDING_DEPLOYMENT"], token, http
            )
            identity = digest([embedder.endpoint, embedder.deployment, embedder.dimensions, args.model_revision])
            vectors = await cached_vectors(
                [r["text"] for r in rows] + [q["text"] for q in queries],
                embedder,
                Cache(directory / "embedding-cache.json"),
                identity,
            )
            for row, vector in zip(rows, vectors[: len(rows)], strict=True):
                row["embedding"] = vector
            for query, vector in zip(queries, vectors[len(rows) :], strict=True):
                query["vector"] = vector
            artifact = {"bundle": bundle, "rows": rows, "queries": queries, "embeddingIdentity": identity}
            artifact["digest"] = digest(artifact)
            filename = "frozen-" + artifact["digest"][:16] + ".json"
            save(directory / filename, artifact)
            save(directory / "current.json", {"artifact": filename})
            stats = {
                "documents": len(documents),
                "rows": len(rows),
                "summaryCalls": summary.calls,
                "summaryCacheHits": summary.hits,
                "summaryPromptTokens": summary.prompt_tokens,
                "summaryCompletionTokens": summary.completion_tokens,
                "embedding": asdict(embedder.usage),
                "seconds": round(perf_counter() - started, 2),
            }
            save(directory / (filename.removesuffix(".json") + "-usage.json"), stats)
            print(json.dumps(stats))
    finally:
        await credential.close()


def artifact_at(directory: Path, name: str | None) -> dict:
    name = name or json.loads((directory / "current.json").read_text())["artifact"]
    if Path(name).name != name:
        raise ValueError("artifact must be a filename in the work directory")
    artifact = json.loads((directory / name).read_text())
    expected = artifact.pop("digest")
    if digest(artifact) != expected or artifact["bundle"]["version"] != VERSION:
        raise ValueError("artifact changed or has an unsupported version; freeze it again")
    artifact["digest"] = expected
    library_id = artifact["bundle"]["libraryId"]
    if not re.fullmatch(r"structure-lab-[a-f0-9]{32}", library_id):
        raise ValueError("only isolated lab snapshots are supported")
    if any(row["libraryId"] != library_id for row in artifact["rows"]):
        raise ValueError("all records must belong to the snapshot's single library")
    return artifact


def receipt_matches(receipt: dict, fingerprint: str, name: str) -> bool:
    return (
        receipt.get("passed") is True
        and receipt.get("targetFingerprint") == fingerprint
        and receipt.get("indexContainer") == name
        and bool(receipt.get("checks"))
        and all(c.get("passed") is True for c in receipt["checks"])
    )


async def verified_containers(database, endpoint: str, database_name: str, receipts: Path) -> dict:
    containers = {}
    records = [json.loads(p.read_text()) for p in receipts.rglob("canary-*.json")]
    for name in CONTAINERS:
        container = database.get_container_client(name)
        actual = await container.read()
        expected = json.loads((REPO / "cosmos" / f"{name}.container.json").read_text())
        if definition_mismatches(expected, actual):
            raise ValueError("experimental container definition mismatch")
        fingerprint = target_fingerprint(endpoint, database_name, name, actual["_rid"])
        if not any(receipt_matches(r, fingerprint, name) for r in records):
            raise ValueError("a matching passing canary receipt is required for every experimental container")
        containers[name] = container
    return containers


async def live(args, directory: Path) -> None:
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    artifact = artifact_at(directory, args.artifact)
    credential = DefaultAzureCredential()
    endpoint, database_name = os.environ["COSMOS_ENDPOINT"], os.environ.get("COSMOS_DATABASE", "fli")
    try:
        async with CosmosClient(endpoint, credential=credential) as client:
            database = client.get_database_client(database_name)
            containers = await verified_containers(database, endpoint, database_name, args.canary_records)
            if args.command == "cleanup":
                from azure.cosmos.exceptions import CosmosResourceNotFoundError

                deleted = 0
                throttle = Throttle()
                for row in artifact["rows"]:
                    for name in ("lab-shared", target_container("split", row["level"])):
                        try:
                            await throttle.run(
                                lambda c=containers[name], r=row: c.delete_item(
                                    r["id"], partition_key=[r["libraryId"], r["documentId"]]
                                )
                            )
                            deleted += 1
                        except CosmosResourceNotFoundError:
                            pass
                print(json.dumps({"deleted": deleted}))
            elif args.command == "load":
                charges = {}
                for name, container in containers.items():
                    rows = [
                        r
                        for r in artifact["rows"]
                        if name == "lab-shared" or (name == "lab-passages") == (r["recordKind"] == "passage")
                    ]
                    throttle = Throttle()
                    started = perf_counter()
                    ru = await write_all(container, rows, throttle, lambda n: None)
                    charges[name] = {
                        "rows": len(rows),
                        "writeRu": ru,
                        "throttledRetries": throttle.count,
                        "seconds": round(perf_counter() - started, 2),
                    }
                save(directory / (artifact["digest"][:16] + "-load.json"), charges)
                print(json.dumps(charges))
            else:
                report = await evaluate(artifact, containers, args.repeats, args.hybrid)
                save(directory / (artifact["digest"][:16] + "-evaluation.json"), report)
                print(
                    json.dumps(
                        {
                            "measurements": len(report["measurements"]),
                            "errors": sum(bool(m["error"]) for m in report["measurements"]),
                            "performanceConclusion": report["performanceConclusion"],
                        }
                    )
                )
    finally:
        await credential.close()


async def evaluate(artifact: dict, containers: dict, repeats: int, hybrid: bool = False) -> dict:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    measurements = []
    library_id = artifact["bundle"]["libraryId"]
    rows = {r["id"]: r for r in artifact["rows"]}
    documents = {d["id"]: d for d in artifact["bundle"]["documents"]}
    tokenizer = TiktokenTokenizer()
    for repeat in range(repeats):
        for qi, q in enumerate(artifact["queries"]):
            # Alternate first layout to reduce fixed ordering bias. Exact runs are
            # references for ANN recall, never a relevance or hybrid gold standard.
            layouts = ("shared", "split") if (repeat + qi) % 2 == 0 else ("split", "shared")
            for layout in layouts:
                container = containers[target_container(layout, q["level"])]
                common = dict(document_id=q.get("documentId"))
                rendered = render_query(library_id, q["vector"], q["level"], q.get("top", 10), **common)
                exact = render_query(library_id, q["vector"], q["level"], q.get("top", 10), exact=True, **common)
                reference = await run_query(
                    container,
                    exact.sql,
                    exact.parameters,
                    library_id,
                    SearchMeasurement("exact-vector", qi),
                    Throttle(),
                    scored=False,
                )
                for mode in ["vector", "hybrid"] if hybrid else ["vector"]:
                    if mode == "hybrid":
                        rendered = render_query(
                            library_id, q["vector"], q["level"], q.get("top", 10), query_text=q["text"], **common
                        )
                    m = await run_query(
                        container,
                        rendered.sql,
                        rendered.parameters,
                        library_id,
                        SearchMeasurement(mode, qi),
                        Throttle(),
                        scored=False,
                    )
                    hits = [rows[rid] for rid in m.result_ids if rid in rows]
                    if len(hits) != len(m.result_ids):
                        raise ValueError("retrieval returned a row outside the frozen artifact")
                    source_groups = {}
                    for row in hits:
                        source_groups.setdefault(row["documentId"], []).append(row["nodeId"])
                    expanded_tokens = sum(
                        source_read(documents[doc], nodes, tokenizer, 10**9)["requiredTokens"]
                        for doc, nodes in source_groups.items()
                    )
                    expected_docs = set(q.get("relevantDocumentIds", []))
                    found_docs = {h["documentId"] for h in hits}
                    measurements.append(
                        asdict(m)
                        | {
                            "layout": layout,
                            "repeat": repeat,
                            "level": q["level"],
                            "annRecall": (
                                recall_at_k(m.result_ids, reference.result_ids, q.get("top", 10))
                                if mode == "vector" and not reference.error
                                else None
                            ),
                            "exactError": reference.error,
                            "exactRu": reference.request_charge,
                            "labelledDocumentRecall": (
                                len(expected_docs & found_docs) / len(expected_docs) if expected_docs else None
                            ),
                            "expandedSourceTokens": expanded_tokens,
                        }
                    )
    return {
        "artifactDigest": artifact["digest"],
        "measurements": measurements,
        "performanceConclusion": "unassessed: inspect per-query scan metrics and index readiness before comparing cost",
        "scope": "retrieval plumbing and labelled candidates; not an agent answer-quality evaluation",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("freeze", "load", "evaluate", "read", "cleanup"))
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--model-revision", default="initial", help="bump when a deployment's underlying model changes")
    parser.add_argument("--artifact")
    parser.add_argument("--canary-records", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--document")
    parser.add_argument("--nodes", nargs="+", default=["n0"])
    parser.add_argument("--max-tokens", type=int, default=32000)
    args = parser.parse_args()
    directory = private_directory(args.work_dir)
    if args.command == "freeze":
        if not args.manifest:
            parser.error("freeze requires --manifest")
        asyncio.run(freeze(args, directory))
    elif args.command == "read":
        artifact = artifact_at(directory, args.artifact)
        document = next(d for d in artifact["bundle"]["documents"] if d["id"] == args.document)
        result = source_read(document, args.nodes, TiktokenTokenizer(), args.max_tokens)
        save(directory / "selected-source.json", result)
        print(json.dumps({k: v for k, v in result.items() if k != "evidence"}))
    else:
        if not args.canary_records:
            parser.error("live operations require --canary-records")
        asyncio.run(live(args, directory))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Provider errors may contain endpoint or content details. Do not print them.
        print(f"Experiment failed ({type(exc).__name__}); no result is claimed.", file=sys.stderr)
        raise SystemExit(1) from None
