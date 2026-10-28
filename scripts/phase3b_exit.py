"""Live image retrieval check. Runtime IDs stay in --state outside the repository.

Requires the running API/worker and the signed-in Azure environment. --model also
checks actual model routing and inline image references. Records contain counts,
costs and booleans only. No prompts, descriptions, answers, SAS URLs or endpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from fli_demo.agent import azure_chat, run_turn
from fli_demo.client import IndexClient
from fli_demo.registry import Registry
from scripts.sample_docs.visual_retrieval import fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--model", action="store_true")
    parser.add_argument("--demo", action="store_true", help="attach a demo agent in the platform registry")
    args = parser.parse_args()
    if args.state.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("--state must be outside the repository")
    client = IndexClient.from_env()
    corpus = fixtures()
    if args.state.exists():
        state = json.loads(args.state.read_text())
    else:
        lib = client.create_library(
            "shared",
            "Visual retrieval demo",
            "Economic outlook, regional profitability and weather charts. "
            "Search for a remembered chart, show it and explain the surrounding text.",
        )
        state = {"libraryId": lib["id"], "documents": []}
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state))
        args.state.chmod(0o600)
    lib = state["libraryId"]
    for name, data, mime in corpus:
        if name not in {d["name"] for d in state["documents"]}:
            doc = client.upload(lib, name, data, mime)
            state["documents"].append(
                {"name": name, "documentId": doc["documentId"], "sha256": hashlib.sha256(data).hexdigest()}
            )
            args.state.write_text(json.dumps(state))
    if args.demo and "agentId" not in state:
        registry = Registry()
        agent = registry.create(
            "official",
            "Visual retrieval demo",
            "Find and explain evidence in the attached library. "
            "Show requested charts inline and cite their file and location.",
        )
        registry.attach(agent["id"], lib)
        state["agentId"] = agent["id"]
        args.state.write_text(json.dumps(state))
    agent_id = state.get("agentId", "phase3b-check")
    record = {
        "check": "phase3b-images",
        "startedAt": datetime.now(UTC).isoformat(),
        "complete": False,
        "services": {},
        "models": [],
        "corpus": [{"type": d["name"].rsplit(".", 1)[-1], "sha256": d.get("sha256")} for d in state["documents"]],
    }

    def save():
        args.record.write_text(json.dumps(record, indent=2))

    save()
    start = time.monotonic()
    while True:
        documents = client.list_documents(lib)
        if any(d["status"] == "failed" for d in documents):
            reasons = [d.get("statusDetail") for d in documents if d["status"] == "failed"]
            raise RuntimeError(f"ingestion failed: {reasons}")
        ready = sum(d["status"] == "ready" for d in documents)
        print(f"Ingestion: {ready}/{len(corpus)} ready", flush=True)
        if ready == len(corpus):
            break
        if time.monotonic() - start > 900:
            raise RuntimeError("ingestion timed out")
        time.sleep(5)
    assert all(d["imageCount"] == 1 for d in documents)
    record["services"]["imageCounts"] = [d["imageCount"] for d in documents]
    record["services"]["ingestionMetrics"] = [d.get("processingMetrics") for d in documents]
    expected = next(d["documentId"] for d in state["documents"] if d["name"].endswith(".pdf"))
    search = client.search(lib, agent_id, "I remember a chart showing GDP growth over time", top=8)
    images = search["attachments"]
    assert any(r["documentId"] == expected and r["image"]["kind"] == "pdf_figure" for r in images)
    with httpx.Client(timeout=30) as fetcher:
        for image in images:
            url = image["image"]["url"]
            params = parse_qs(urlsplit(url).query)
            assert params["sp"] == ["r"] and params["sr"] == ["b"] and params["spr"] == ["https"]
            assert "skoid" in params and "sig" in params  # user delegation, not account-key SAS
            response = fetcher.get(url)
            response.raise_for_status()
            assert (
                response.headers["content-type"].startswith("image/") and response.content[:8] == b"\x89PNG\r\n\x1a\n"
            )
    record["services"]["vagueSearch"] = {
        "imageResults": len(images),
        "expectedVisualFound": True,
        "delegationReadLinksFetched": True,
        "usage": search["metadata"]["usage"],
    }
    full = client.read_documents(lib, agent_id, documents=[{"documentId": expected}])
    assert full["evidence"]["complete"] and full["attachments"]
    assert "productivity" in json.dumps(full).lower()
    record["services"]["completeRead"] = {
        "imageIncluded": True,
        "sourceExplanationIncluded": True,
        "estimatedTokens": full["metadata"]["budget"]["requiredTokens"],
        "usage": full["metadata"]["usage"],
    }
    search = client.search(lib, agent_id, "Show me the regional margin chart", filters={"contentKinds": ["image"]})
    deck = next(d["documentId"] for d in state["documents"] if d["name"].endswith(".pptx"))
    assert any(r["documentId"] == deck and r.get("image") for r in search["evidence"]["results"])
    record["services"]["deckChartFound"] = True
    save()
    print("Live image search, signed reads, full-document visuals and deck retrieval passed", flush=True)
    if args.model:
        chat = azure_chat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"])
        agent = {"id": agent_id, "libraryIds": [lib], "instructions": "Answer using the attached file library."}
        for question in [
            "I remember there was a chart in one of these documents that showed something about GDP growth over time. "
            "Can you find it, show it and explain the discussion around it?",
            "Show me the regional margin chart from the deck and explain the regional differences.",
        ]:
            turn = run_turn(chat, client, agent, [], question, image_inputs=True)
            visuals = [i for trace in turn.searches for i in trace.images]
            assert visuals and "fli-image:" in turn.answer
            assert any(isinstance(m.get("content"), list) for m in turn.messages)
            record["models"].append(
                {
                    "imageReturned": True,
                    "inlineImageReference": True,
                    "visualInputSupplied": True,
                    "operations": [s.operation for s in turn.searches],
                    "toolCalls": len(turn.searches),
                    "requestCharge": sum(s.request_charge or 0 for s in turn.searches),
                    "totalMs": turn.total_ms,
                    "usage": [c["usage"] for c in turn.model_calls],
                }
            )
            # Local-only answer evidence for human inspection; never copied into records.
            review = args.state.with_name(f"{args.state.stem}-answer-{len(record['models'])}.json")
            review.write_text(json.dumps({"answer": turn.answer, "images": visuals}, indent=2))
            save()
            print(f"Model scenario {len(record['models'])}: chart retrieved and placed inline", flush=True)
    record["complete"] = True
    save()
    print("Phase 3b live check complete", flush=True)


if __name__ == "__main__":
    main()
