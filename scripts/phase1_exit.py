"""Phase 1 exit check (docs/build-plan.md), driven through the running API over REST.

    python scripts/phase1_exit.py ~/corpus-a ~/corpus-b

1. Creates a shared library and uploads every file under the given paths.
2. Waits until no document is queued or processing.
3. Runs the searches below and prints the top results (file, location, heading, opening
   words) for a person to judge relevance. Relevance is not asserted: results vary between
   identical searches (evidence.md E14), and hybrid search has no exact reference (E24).
4. Creates a personal library bound to one agent, and checks that another agent is refused.
5. Writes ``records/phase1-exit-<UTC timestamp>.json``: metrics only. No file names, ids,
   query text or chunk text are written (CLAUDE.md rule 12).

Needs the API running locally with AUTH_MODE=dev and the worker running against the same
deployment. ``DEV_API_KEY`` must match the API's.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent

# Questions written for the Phase 1 corpus; each names the kind of document it should find.
QUESTIONS = [
    ("pdf", "What were the main drivers of cash flow from operations this year?"),
    ("pdf", "How does the company describe its climate and energy transition targets?"),
    (
        "docx",
        "What does the impact assessment say about homelessness eligibility for people subject to immigration control?",
    ),
    ("docx", "What concerns did respondents raise about taxi licensing across local authorities?"),
    ("docx", "How should a response to the deposit return scheme consultation be submitted?"),
    ("csv", "What was the GDP of Wales or the United Kingdom in 2010?"),
    ("txt", "Who is Mr Darcy and how does Elizabeth first regard him?"),
    ("txt", "What happens when the Pequod meets the white whale?"),
    ("md", "Which design decisions were recorded about the benchmark's scale?"),
    ("html", "How is gross margin calculated?"),
    ("eml", "What was discussed on the mailing list about the software problem?"),
]

WRONG_AGENT_SEARCH = "What is in this library?"


def wait_for_documents(client: httpx.Client, library_id: str, timeout: float) -> list[dict]:
    deadline = time.monotonic() + timeout
    while True:
        docs = client.get(f"/v1/libraries/{library_id}/documents").raise_for_status().json()["documents"]
        pending = [d for d in docs if d["status"] in ("queued", "processing")]
        counts = Counter(d["status"] for d in docs)
        print(f"  {dict(sorted(counts.items()))}", flush=True)
        if not pending:
            return docs
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out with {len(pending)} documents still pending")
        time.sleep(10)


def upload(client: httpx.Client, library_id: str, path: Path) -> str:
    with path.open("rb") as handle:
        r = client.post(
            f"/v1/libraries/{library_id}/documents",
            files={"file": (path.name, handle, "application/octet-stream")},
            timeout=300,
        )
    r.raise_for_status()
    return r.json()["documentId"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", type=Path, help="files or directories to upload")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=3600, help="seconds to wait for ingestion")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    key = os.environ.get("DEV_API_KEY")
    if not key:
        print("DEV_API_KEY must be set (the same value as the API's)", file=sys.stderr)
        return 2
    files = sorted(p for root in args.paths for p in ([root] if root.is_file() else root.rglob("*")) if p.is_file())
    files = [p for p in files if not p.name.startswith(".")]
    if not files:
        print("no files found", file=sys.stderr)
        return 2

    record: dict = {"startedAt": datetime.now(UTC).isoformat(), "fileCount": len(files)}
    client = httpx.Client(base_url=args.api, headers={"Authorization": f"Bearer {key}"}, timeout=60)

    print(f"1. creating a shared library and uploading {len(files)} files")
    library = (
        client.post(
            "/v1/libraries",
            json={"kind": "shared", "name": "Phase 1 exit check", "description": "Mixed public documents."},
        )
        .raise_for_status()
        .json()
    )
    lib = library["id"]
    started = time.monotonic()
    for path in files:
        upload(client, lib, path)
    record["uploadSeconds"] = round(time.monotonic() - started, 1)

    print("2. waiting for ingestion")
    docs = wait_for_documents(client, lib, args.timeout)
    record["ingestSeconds"] = round(time.monotonic() - started, 1)
    by_type: dict[str, Counter] = {}
    for d in docs:
        by_type.setdefault(d["fileType"], Counter())[d["status"]] += 1
    record["documentsByType"] = {t: dict(c) for t, c in sorted(by_type.items())}
    record["failedDetails"] = dict(Counter(d["statusDetail"] for d in docs if d["status"] == "failed"))
    record["chunksByType"] = dict(sorted(_chunks_by_type(docs).items()))
    record["readyDocuments"] = sum(1 for d in docs if d["status"] == "ready")
    for d in docs:
        mark = "ok " if d["status"] == "ready" else "!! "
        print(
            f"  {mark}{d['fileType']:5} {d['status']:7} chunks={d['chunkCount']:5} {d['fileName']}"
            + (f"  ({d['statusDetail']})" if d["statusDetail"] else "")
        )

    print("3. searching (judge relevance below; nothing here is asserted)")
    searches = []
    for expected_type, question in QUESTIONS:
        t0 = time.monotonic()
        r = client.post(
            f"/v1/libraries/{lib}/search",
            json={"query": question, "top": args.top},
            headers={"X-Agent-Id": "exit-check-agent"},
        )
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        r.raise_for_status()
        body = r.json()
        results = body["results"]
        searches.append(
            {
                "expectedType": expected_type,
                "status": r.status_code,
                "resultCount": len(results),
                "disposition": body["coverage"]["disposition"],
                "clientMs": elapsed,
                "allHaveFileName": all(bool(x["fileName"]) for x in results),
                "allHaveLocation": all(bool(x["location"]) for x in results),
                "topFileTypes": [x["fileName"].rsplit(".", 1)[-1].lower() for x in results],
            }
        )
        print(f"\n  Q ({expected_type}): {question}   [{elapsed} ms]")
        for x in results[:3]:
            snippet = " ".join(x["text"].split())[:160]
            print(f"    {x['rank']}. {x['fileName']}  {json.dumps(x['location'])}  [{x['heading'] or ''}]")
            print(f"       {snippet}")
    record["searches"] = searches

    print("\n4. personal library refuses another agent")
    personal = (
        client.post(
            "/v1/libraries",
            json={
                "kind": "personal",
                "boundAgentId": "exit-agent-a",
                "name": "Exit check personal",
                "description": "x",
            },
        )
        .raise_for_status()
        .json()
    )
    upload(client, personal["id"], files[0])
    own = client.post(
        f"/v1/libraries/{personal['id']}/search",
        json={"query": WRONG_AGENT_SEARCH},
        headers={"X-Agent-Id": "exit-agent-a"},
    )
    other = client.post(
        f"/v1/libraries/{personal['id']}/search",
        json={"query": WRONG_AGENT_SEARCH},
        headers={"X-Agent-Id": "exit-agent-b"},
    )
    record["personal"] = {
        "boundAgentStatus": own.status_code,
        "otherAgentStatus": other.status_code,
        "otherAgentCode": other.json().get("error", {}).get("code"),
    }
    print(f"  bound agent: {own.status_code}   other agent: {other.status_code} {record['personal']['otherAgentCode']}")

    record["finishedAt"] = datetime.now(UTC).isoformat()
    out = REPO / "records" / f"phase1-exit-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nrecord: {out.relative_to(REPO)}")
    print("The libraries are left in place for Phase 2. Delete them with DELETE /v1/libraries/{id} when done.")
    return 0


def _chunks_by_type(docs: list[dict]) -> dict[str, int]:
    totals: Counter = Counter()
    for d in docs:
        totals[d["fileType"]] += d.get("chunkCount", 0)
    return dict(totals)


if __name__ == "__main__":
    sys.exit(main())
