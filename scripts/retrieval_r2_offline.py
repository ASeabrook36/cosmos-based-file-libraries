"""Preserve model-window attestations and recheck frozen R2 vectors offline.

No Azure calls. Historical ARM receipts are validated against the measured window,
not incorrectly treated as fresh current deployment evidence after cleanup.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fli.chunking import TiktokenTokenizer
from fli.query import to_float16_query
from fli.structure_lab import digest
from fli.structure_representation import VARIANTS, source_relevance
from scripts.retrieval_controls import deployment_binding, private_path
from scripts.retrieval_r0_corpus import sha256
from scripts.retrieval_r2 import load_source, selection_grade
from scripts.structure_lab import save


def read(path):
    return json.loads(Path(path).read_text())


def window_binding(before, after, *, started, finished, endpoint, deployment, model, revision):
    """Historical validity is anchored to actual measurement times, never today's clock."""
    before_time = datetime.fromisoformat(before["capturedAt"])
    after_time = datetime.fromisoformat(after["capturedAt"])
    if not started <= finished <= after_time <= finished + timedelta(minutes=15):
        raise ValueError("after receipt is not adjacent to the measured window")
    if not before_time <= started:
        raise ValueError("before receipt follows the measurement start")
    first = deployment_binding(
        before,
        configured_endpoint=endpoint,
        configured_deployment=deployment,
        model=model,
        revision=revision,
        now=started,
    )
    last = deployment_binding(
        after,
        configured_endpoint=endpoint,
        configured_deployment=deployment,
        model=model,
        revision=revision,
        now=after_time,
    )
    if first != last:
        raise ValueError("model or SKU changed during the measured window")
    return first


def finalize(source_dir, before_path, after_path):
    source = load_source(source_dir)
    vectors, result = read(source_dir / "r2-vectors.json"), read(source_dir / "r2-evaluation.json")
    attempts = [(p, read(p)) for p in source_dir.glob("r2-embedding-attempt-*.json")]
    completed = [(p, value) for p, value in attempts if value.get("status") == "complete"]
    if len(completed) != 1 or result.get("status") != "complete":
        raise ValueError("one completed immutable vector freeze and evaluated result required")
    path, attempt = completed[0]
    match = re.fullmatch(r"r2-embedding-attempt-(\d+)\.json", path.name)
    if not match:
        raise ValueError("measurement completion timestamp unavailable")
    finished = datetime.fromtimestamp(int(match[1]) / 1e9, UTC)
    started = finished - timedelta(milliseconds=attempt["elapsedMs"])
    before, after = read(private_path(before_path)), read(private_path(after_path))
    private = vectors["privateBinding"]
    binding = window_binding(
        before,
        after,
        started=started,
        finished=finished,
        endpoint=private["endpoint"],
        deployment=private["deployment"],
        model="text-embedding-3-large",
        revision="1",
    )
    if binding["bindingSha256"] != vectors["embeddingIdentity"] or result["sourceArtifactDigest"] != source["digest"]:
        raise ValueError("window receipt does not bind the frozen comparison")
    directory = source_dir / "offline-attestation"
    if directory.exists():
        raise ValueError("offline attestation already exists")
    directory.mkdir(mode=0o700)
    save(directory / "before.json", before)
    save(directory / "after.json", after)
    save(directory / "attempt.json", attempt)
    artifact = {
        "schema": "fli.r2.offline-attestation.v1",
        "modelWindowVerified": True,
        "startedAt": started.isoformat(),
        "finishedAt": finished.isoformat(),
        "binding": binding,
        "beforeSha256": digest(before),
        "afterSha256": digest(after),
        "attemptSha256": digest(attempt),
        "sourceArtifactDigest": source["digest"],
        "vectorsDigest": vectors["digest"],
        "vectorArraySha256": vectors["arraySha256"],
        "resultDigest": digest(result),
        "interpretation": (
            "historical measured-window validation, not current cloud inventory or current model settings"
        ),
    }
    artifact["digest"] = digest(artifact)
    save(directory / "attestation.json", artifact)
    print(json.dumps({"status": "finalized", "modelWindowVerified": True, "attestationDigest": artifact["digest"]}))


def recheck(source_dir, output):
    import numpy as np

    source = load_source(source_dir)
    directory = source_dir / "offline-attestation"
    attestation = read(directory / "attestation.json")
    expected = attestation.pop("digest")
    if digest(attestation) != expected:
        raise ValueError("offline attestation changed")
    before, after, attempt = [read(directory / name) for name in ("before.json", "after.json", "attempt.json")]
    if any(
        digest(value) != attestation[key]
        for value, key in ((before, "beforeSha256"), (after, "afterSha256"), (attempt, "attemptSha256"))
    ):
        raise ValueError("historical receipts changed")
    vectors, original = read(source_dir / "r2-vectors.json"), read(source_dir / "r2-evaluation.json")
    vd = vectors.pop("digest")
    if digest(vectors) != vd or vd != attestation["vectorsDigest"] or digest(original) != attestation["resultDigest"]:
        raise ValueError("frozen vector/result metadata changed")
    if source["digest"] != attestation["sourceArtifactDigest"]:
        raise ValueError("frozen sources changed")
    private = vectors["privateBinding"]
    binding = window_binding(
        before,
        after,
        started=datetime.fromisoformat(attestation["startedAt"]),
        finished=datetime.fromisoformat(attestation["finishedAt"]),
        endpoint=private["endpoint"],
        deployment=private["deployment"],
        model="text-embedding-3-large",
        revision="1",
    )
    if binding != attestation["binding"]:
        raise ValueError("historical model binding changed")
    array_path = source_dir / vectors["arrayFile"]
    if array_path.name != "r2-vectors.npy" or sha256(array_path.read_bytes()) != attestation["vectorArraySha256"]:
        raise ValueError("frozen vector bytes changed")
    array = np.load(array_path, mmap_mode="r", allow_pickle=False)
    docs, tok = {d["id"]: d for d in source["bundle"]["documents"]}, TiktokenTokenizer()
    original_rows = {(m["variant"], m["case"]): m for m in original["measurements"]}
    verified = []
    for variant in VARIANTS:
        rows = source["variants"][variant]
        matrix = np.asarray([to_float16_query(array[vectors["rows"][row["id"]]]) for row in rows], dtype=np.float64)
        matrix /= np.linalg.norm(matrix, axis=1)[:, None]
        for task in source["tasks"]:
            query = np.asarray(to_float16_query(array[vectors["queries"][task["case"]]]), dtype=np.float64)
            scores = matrix @ (query / np.linalg.norm(query))
            selected = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i]["id"]))[:8]
            hits = [rows[i] for i in selected]
            measured = source_relevance(task, docs, hits, tok)
            prior = original_rows[(variant, task["case"])]
            if any(prior[key] != value for key, value in measured.items()) or prior["resultIds"] != [
                r["id"] for r in hits
            ]:
                raise ValueError("offline source relevance differs from original result")
            verified.append(prior)
    grade = selection_grade(verified, {variant: len(rows) for variant, rows in source["variants"].items()})
    if any(original[key] != value for key, value in grade.items()):
        raise ValueError("offline selection grade differs")
    result = {
        "schema": "fli.r2.offline-recheck.v1",
        "status": "complete",
        "attestationDigest": expected,
        "verifiedMeasurements": len(verified),
        "heldoutExecuted": False,
        "cloudCalls": 0,
        "allFrozenResultsReproduced": True,
        "selectionGrade": grade,
    }
    output = private_path(output)
    if output.exists():
        raise ValueError("offline recheck output must be new")
    save(output, result)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("finalize", "recheck"))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source_dir = private_path(args.source_dir)
    if args.command == "finalize":
        finalize(source_dir, args.before, args.after)
    else:
        recheck(source_dir, args.output)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "errorType": type(exc).__name__}))
        raise SystemExit(1) from None
