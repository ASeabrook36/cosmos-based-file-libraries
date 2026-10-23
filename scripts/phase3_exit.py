"""Phase 3 exit check (docs/build-plan.md), driven through the running API over REST.

    python scripts/phase3_exit.py lifecycle OLD.pdf NEW.pdf \\
        --old-question "…answered only by OLD…" --new-question "…answered only by NEW…"

``lifecycle`` checks document replacement, then deletion:

1. Creates a shared library and uploads OLD. Waits until it is ready.
2. Replaces it with NEW under OLD's file name (review finding 7: a same-name replacement must
   not overwrite the original still being served).
3. Searches both questions every few seconds while the replacement is written, and for
   ``--after`` seconds once it has committed, to cover the purge. A search sent after the API
   has shown the commit must return nothing of generation 1; one sent before it must return
   nothing of generation 2. Chunk ids carry their generation, so this needs no chunk text.
4. Counts searches whose coverage reports results left out (review finding 6).
5. Deletes the document, then the library, and checks each disappears from search at once.
6. Writes ``records/phase3-exit-lifecycle-<UTC timestamp>.json``: metrics and file hashes
   only. No file names, query text or chunk text (CLAUDE.md rule 12).

Use two real, different documents (evidence.md E14), each with a question only it answers.
Needs the API running locally with AUTH_MODE=dev and the worker running against the same
deployment. ``DEV_API_KEY`` must match the API's.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent

AGENT = {"X-Agent-Id": "exit-check-agent"}
LIBRARY_NAME = "Phase 3 exit check: lifecycle"


def generation_of(chunk_id: str) -> int:
    return int(chunk_id.split(":")[1])  # {documentId}:{generation}:{chunkIndex}


def get_document(client: httpx.Client, library_id: str, document_id: str) -> dict:
    return client.get(f"/v1/libraries/{library_id}/documents/{document_id}").raise_for_status().json()


def wait_until(client: httpx.Client, library_id: str, document_id: str, done, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while not done(document := get_document(client, library_id, document_id)):
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out: document is {document['status']}")
        time.sleep(5)
    return document


def search(client: httpx.Client, library_id: str, question: str, top: int) -> httpx.Response:
    return client.post(f"/v1/libraries/{library_id}/search", json={"query": question, "top": top}, headers=AGENT)


def observe(client: httpx.Client, library_id: str, document_id: str, question: str, top: int) -> dict:
    """One search, labelled with the active generation the API showed just before it was sent."""
    active = get_document(client, library_id, document_id)["activeGeneration"]
    r = search(client, library_id, question, top).raise_for_status()
    body = r.json()
    returned = sorted({generation_of(x["chunkId"]) for x in body["results"] if x["documentId"] == document_id})
    return {
        "activeBefore": active,
        "generations": returned,
        "resultCount": body["coverage"]["resultCount"],
        "disposition": body["coverage"]["disposition"],
        "underfilled": any("left out" in note for note in body["coverage"]["notes"]),
        "requestCharge": body["usage"]["requestCharge"],
    }


def lifecycle(client: httpx.Client, args: argparse.Namespace) -> dict:
    old, new = args.old.read_bytes(), args.new.read_bytes()
    questions = {"old": args.old_question, "new": args.new_question}
    record: dict = {
        "check": "lifecycle",
        "startedAt": datetime.now(UTC).isoformat(),
        "oldSha256": hashlib.sha256(old).hexdigest(),
        "newSha256": hashlib.sha256(new).hexdigest(),
        "sameFileName": True,
    }
    name = args.old.name

    print("1. creating a shared library and uploading the old document")
    library = client.post("/v1/libraries", json={"kind": "shared", "name": LIBRARY_NAME}).raise_for_status().json()
    lib = library["id"]
    files = {"file": (name, old, "application/octet-stream")}
    doc_id = (
        client.post(f"/v1/libraries/{lib}/documents", files=files, timeout=300).raise_for_status().json()["documentId"]
    )
    first = wait_until(client, lib, doc_id, lambda d: d["status"] not in ("queued", "processing"), args.timeout)
    if first["activeGeneration"] != 1:
        raise SystemExit(f"the old document did not become ready: {first['status']} {first['statusDetail']}")
    record["before"] = {k: observe(client, lib, doc_id, q, args.top) for k, q in questions.items()}

    print("2. replacing it with the new document under the same file name")
    started = time.monotonic()
    files = {"file": (name, new, "application/octet-stream")}
    put = client.put(f"/v1/libraries/{lib}/documents/{doc_id}/content", files=files, timeout=300)
    put.raise_for_status()
    new_generation = record["replaceGeneration"] = put.json()["generation"]

    print("3. searching while the replacement is written, then after it commits")
    observations, committed_at = [], None
    while True:
        elapsed = time.monotonic() - started
        if elapsed > args.timeout:
            raise SystemExit("timed out waiting for the replacement to commit")
        for key, question in questions.items():
            seen = observe(client, lib, doc_id, question, args.top)
            seen.update(question=key, secondsSinceReplace=round(time.monotonic() - started, 1))
            observations.append(seen)
            if seen["activeBefore"] == new_generation and committed_at is None:
                committed_at = time.monotonic()
                print(f"  committed after {round(committed_at - started)} s", flush=True)
        document = get_document(client, lib, doc_id)
        if document["status"] == "failed":
            raise SystemExit(f"the replacement failed: {document['statusDetail']}")
        if committed_at is not None and time.monotonic() - committed_at > args.after:
            break
        time.sleep(args.interval)
    leaks = [o for o in observations if o["activeBefore"] == new_generation and 1 in o["generations"]]
    early = [o for o in observations if o["activeBefore"] == 1 and new_generation in o["generations"]]
    record["replacement"] = {
        "commitSeconds": round(committed_at - started, 1) if committed_at else None,
        "searches": len(observations),
        "searchesAfterCommit": sum(1 for o in observations if o["activeBefore"] == new_generation),
        "oldGenerationAfterCommit": len(leaks),
        "newGenerationBeforeCommit": len(early),
        "underfilledSearches": sum(1 for o in observations if o["underfilled"]),
        "observations": observations,
    }
    final = get_document(client, lib, doc_id)
    record["afterReplace"] = {
        "activeGeneration": final["activeGeneration"],
        "pendingFile": final["pendingFile"] is not None,
        "blobPathHasGeneration": f"/original/{final['activeGeneration']}/" in final["blobPath"],
        "sha256MatchesNew": final["sha256"] == record["newSha256"],
    }
    print(f"  {len(leaks)} searches returned the old generation after the commit (must be 0)")
    print(f"  {record['replacement']['underfilledSearches']} searches reported results left out")

    print("4. deleting the document, then the library")
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}").raise_for_status()
    gone = [search(client, lib, q, args.top).raise_for_status().json() for q in questions.values()]
    client.delete(f"/v1/libraries/{lib}").raise_for_status()
    refused = search(client, lib, questions["new"], args.top)
    record["deletion"] = {
        "documentResultsAfterDelete": sum(1 for body in gone for x in body["results"] if x["documentId"] == doc_id),
        "libraryStatusAfterDelete": refused.status_code,
        "libraryCodeAfterDelete": refused.json().get("error", {}).get("code"),
    }
    record["passed"] = (
        not leaks
        and not early
        and record["afterReplace"]
        == {"activeGeneration": 2, "pendingFile": False, "blobPathHasGeneration": True, "sha256MatchesNew": True}
        and record["deletion"]["documentResultsAfterDelete"] == 0
        and record["deletion"]["libraryStatusAfterDelete"] == 404
    )
    record["finishedAt"] = datetime.now(UTC).isoformat()
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="check", required=True)
    life = sub.add_parser("lifecycle", help="replacement and deletion")
    life.add_argument("old", type=Path, help="the document first uploaded")
    life.add_argument("new", type=Path, help="the document that replaces it, uploaded under the old file name")
    life.add_argument("--old-question", required=True, help="a question only the old document answers")
    life.add_argument("--new-question", required=True, help="a question only the new document answers")
    life.add_argument("--after", type=float, default=120, help="seconds to keep searching after the commit")
    life.add_argument("--interval", type=float, default=2, help="seconds between rounds of searches")
    life.add_argument("--api", default="http://127.0.0.1:8000")
    life.add_argument("--timeout", type=float, default=1800, help="seconds to wait for ingestion")
    life.add_argument("--top", type=int, default=8)
    args = parser.parse_args()

    key = os.environ.get("DEV_API_KEY")
    if not key:
        print("DEV_API_KEY must be set (the same value as the API's)", file=sys.stderr)
        return 2
    client = httpx.Client(base_url=args.api, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    record = lifecycle(client, args)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPO / "records" / f"phase3-exit-{args.check}-{stamp}.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nrecord: {out.relative_to(REPO)}   passed: {record['passed']}")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
