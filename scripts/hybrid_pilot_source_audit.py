"""Offline audit of a captured pilot publication, including post-commit errors.

Does not suppress failed attempts or assert broker quiescence. An errored attempt
can coexist with a valid committed generation; require independent publication
and stored-content evidence before accepting that narrowly defined recovery.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from fli.checkpoints import digest as publication_digest
from fli.structure_lab import digest
from scripts.retrieval_controls import private_path


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def reference(path):
    path = private_path(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def complete_inventory(rows, inventory):
    require(len(rows) == len(inventory), "archive manifest inventory count differs")
    require(
        [{k: row.get(k) for k in item} for row, item in zip(rows, inventory, strict=True)] == inventory,
        "archive manifest differs from captured inventory",
    )


def classify(attempts, job, source_hash, metrics):
    bound = [a for a in attempts if a.get("executionId") == job["executionId"]]
    require(bound, "no source-job-bound attempt")
    require(all(a.get("sourceSha256") == source_hash for a in bound), "attempt source mismatch")
    if any(a["status"] == "published" for a in bound):
        return "source-bound-published"
    require(len(bound) == 1, "ambiguous unpublished source attempts")
    attempt = bound[0]
    require(
        attempt["status"] == "error"
        and attempt.get("errorType") == "ServiceBusError"
        and attempt.get("chunkWriteChargeComplete") is True
        and isinstance(attempt.get("chunkWriteChargeKnown"), (int, float))
        and metrics.get("executionId") == job["executionId"],
        "no narrowly evidenced post-publication recovery",
    )
    recovered = [
        a
        for a in attempts
        if a.get("executionId") is None
        and a["status"] == "published"
        and a["startedAt"] > attempt["finishedAt"]
        and not any(k in a for k in ("chunkWriteChargeKnown", "providerResultUsage", "localEmbeddingUsage"))
    ]
    require(recovered, "no later no-work published observation")
    return "committed-generation-with-terminal-error-and-later-no-work-observation"


def audit(archive, snapshot_path, output):
    archive = private_path(archive)
    snapshot = read(private_path(snapshot_path))
    owner = read(archive / "ownership-before.json")
    rows = read(archive / "blob-manifest-private.json")
    complete_inventory(rows, read(archive / "blob-paths-before-private.json"))
    require(
        read(archive / "blob-paths-before-private.json") == read(archive / "blob-paths-after-private.json"),
        "blob inventory changed during capture",
    )
    docs = read(archive / "documents-before-private.json")
    require(docs == read(archive / "documents-after-private.json"), "documents changed during capture")
    require(
        read(archive / "library-before-private.json") == read(archive / "library-after-private.json"),
        "library changed during capture",
    )
    require(
        read(archive / "library-before-private.json")["id"] == snapshot["library"]["id"] == owner["library"]["id"],
        "owned library identity differs",
    )
    require(
        owner["createdByThisRun"] and owner["readyCompleted"] and not owner.get("deleteAttempted"),
        "owned ready publication required",
    )
    by_path = {}
    for row in rows:
        path = archive / row["archiveFile"]
        require(path.parent == archive and reference(path)["sha256"] == row["sha256"], "archive bytes differ")
        require(path.stat().st_size == row["bytes"], "archive size differs")
        by_path[row["blobPath"]] = path
    require(len(by_path) == len(rows), "duplicate archive path")
    actual_docs = {d["id"]: d for d in snapshot["documents"]}
    require(
        set(actual_docs) == {d["id"] for d in docs} == {v["documentId"] for v in owner["sources"].values()},
        "document set differs",
    )
    chunks = {(c["documentId"], c["chunkIndex"]): c for c in snapshot["chunks"]}
    require(len(chunks) == len(snapshot["chunks"]), "duplicate stored chunk identity")
    sources, attempts_all = [], []
    for source_id, owned in owner["sources"].items():
        doc = next(d for d in docs if d["id"] == owned["documentId"])
        fresh = actual_docs[doc["id"]]
        require(all(fresh.get(k) == v for k, v in doc.items() if not k.startswith("_")), "publication changed")
        require(
            doc["status"] == "ready" and doc["activeGeneration"] == 1 and not doc.get("pendingGeneration"),
            "uncommitted generation",
        )
        prefix = owner["library"]["id"] + "/" + doc["id"] + "/"
        plans = [p for name, p in by_path.items() if name.startswith(prefix) and name.endswith("/plan.json")]
        require(len(plans) == 1, "ambiguous generation plan")
        plan = read(plans[0])
        require(
            plan["job"] == doc["ingestionJob"] and plan["sourceSha256"] == doc["sha256"] and plan["generation"] == 1,
            "plan/source binding differs",
        )
        require(reference(by_path[doc["blobPath"]])["sha256"] == doc["sha256"], "original differs")
        require(len(plan["pieces"]) == len(plan["vectors"]) == doc["chunkCount"], "publication count differs")
        require(read(by_path[doc["fullText"]["blobPath"]]) == plan["sections"], "published sections differ")
        for piece in plan["pieces"]:
            chunk = chunks.pop((doc["id"], piece["index"]))
            expected = {
                "text": piece["text"],
                "heading": piece["heading"],
                "location": piece["location"] or {},
                "imageRef": None,
                "imageMeta": None,
                "embeddingModel": "text-embedding-3-large",
                "chunkingVersion": plan["chunkingVersion"],
            }
            require(
                not plan["imageRecords"]
                and chunk["generation"] == 1
                and chunk["contentHash"] == publication_digest(expected),
                "stored chunk content differs from plan",
            )
        parts = {}
        for name, path in by_path.items():
            if name.startswith(prefix + "attempts/"):
                bits = name[len(prefix + "attempts/") :].split("/")
                require(len(bits) == 2 and bits[1] in {"start.json", "finish.json"}, "unknown attempt artifact")
                parts.setdefault(bits[0], {})[bits[1]] = read(path)
        attempts = []
        for pair in parts.values():
            require(set(pair) == {"start.json", "finish.json"}, "nonterminal attempt")
            start, finish = pair["start.json"], pair["finish.json"]
            require(
                start["attemptId"] == finish["attemptId"]
                and start["startedAt"] == finish["startedAt"]
                and start["generation"] == finish["generation"] == 1
                and finish["status"] in {"published", "error", "failed", "stopped"}
                and start["startedAt"] <= finish["finishedAt"],
                "attempt identity differs",
            )
            attempts.append(finish)
        category = classify(attempts, plan["job"], doc["sha256"], doc["processingMetrics"])
        sources.append({"sourceId": source_id, "chunks": doc["chunkCount"], "publicationEvidence": category})
        attempts_all.extend({"sourceId": source_id, **a} for a in attempts)
    require(not chunks, "unexpected stored chunks")
    receipt = {
        "schema": "fli.hybrid-model-pilot.source-audit.v1",
        "status": "accepted",
        "capturedAt": datetime.now(UTC).isoformat(),
        "sources": sources,
        "allObservedAttemptsTerminal": True,
        "storedChunkContentMatchesImmutablePlans": True,
        "workerAttempts": len(attempts_all),
        "terminalErrors": sum(a["status"] == "error" for a in attempts_all),
        "knownInstrumentedChunkStoreRu": sum(a.get("chunkWriteChargeKnown", 0) for a in attempts_all),
        "sourceSnapshotSha256": digest(snapshot),
        "evidenceReferences": [
            reference(snapshot_path),
            *[
                reference(archive / n)
                for n in (
                    "ownership-before.json",
                    "blob-manifest-private.json",
                    "documents-before-private.json",
                    "documents-after-private.json",
                    "library-before-private.json",
                    "library-after-private.json",
                    "blob-paths-before-private.json",
                    "blob-paths-after-private.json",
                    "failure-private.json",
                )
            ],
        ],
        "producerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "Original archive rejection preserved; no error relabeled as a successful attempt.",
            "Observed committed publication and terminal retry, not proof of broker quiescence.",
            "Chunk content identity checked; no full vector projection or billed cost claim.",
        ],
    }
    target = private_path(output)
    with target.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    target.chmod(0o600)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("archive", "snapshot", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.archive, args.snapshot, args.output)
    print(
        json.dumps(
            {k: result[k] for k in ("status", "workerAttempts", "terminalErrors", "knownInstrumentedChunkStoreRu")}
        )
    )
