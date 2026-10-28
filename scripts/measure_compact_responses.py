"""Reproducible local response-text comparison. No credentials, live models or Cosmos.

Run: python -m scripts.measure_compact_responses > records/compact-rest-responses.json
Uses the existing invented chart fixtures and in-memory contract-test services. Only
aggregate token counts/booleans are emitted, never source text, identifiers or links.
"""

import asyncio
import json

from fli.chunking import ApproxTokenizer, Chunker, default_tokenizer
from fli.presentation import evidence_json
from fli.worker.handlers import Worker
from scripts.sample_docs.visual_retrieval import gdp_pdf, margin_office
from tests.fakes import FakeChat, FakeEmbedder, api
from tests.layout_fakes import FakeLayout


def texts(value):
    if isinstance(value, dict):
        text = value.get("text")
        if text and value.get("image") and "chunkId" in value and text.startswith("Image from "):
            marker = f", under '{value.get('heading') or ''}': "
            text = text.split(marker, 1)[-1]
        return ([text] if text is not None else []) + [t for v in value.values() for t in texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in texts(v)]
    return []


async def measure():
    client, services = api()
    client.headers["X-Agent-Id"] = "agent"
    lib = client.post("/v1/libraries", json={"kind": "shared", "name": "Invented evidence"}).json()["id"]
    worker = Worker(
        services.libraries,
        services.documents,
        services.chunks,
        services.blobs,
        services.queue,
        FakeEmbedder(),
        Chunker(ApproxTokenizer()),
    )
    worker.layout = FakeLayout()
    worker.vision = FakeChat({"description": "A chart of GDP growth with a contraction followed by a rebound."})
    ids = []
    for name, data in [("outlook.pdf", gdp_pdf()), ("margins.docx", margin_office("docx"))]:
        ids.append(client.post(f"/v1/libraries/{lib}/documents", files={"file": (name, data)}).json()["documentId"])
    while services.queue.sent:
        _, message = services.queue.sent.pop(0)
        await worker.handle(message)
    tokenizer, rows = default_tokenizer(), []
    operations = [
        ("catalogue", "documents:discover", {}),
        ("semantic_candidates", "documents:discover", {"mode": "semantic_candidates", "query": "growth"}),
        ("search_with_neighbors", "search", {"query": "growth", "includeNeighbors": True}),
        ("full_pdf", "documents:read", {"documents": [{"documentId": ids[0]}]}),
        ("full_office", "documents:read", {"documents": [{"documentId": ids[1]}]}),
        ("selected_pdf", "documents:read", {"mode": "selected", "documents": [{"documentId": ids[0], "pages": [1]}]}),
        ("outline_pdf", "documents:read", {"mode": "outline", "documents": [{"documentId": ids[0]}]}),
        ("image_inspection", "documents:images", {"documentId": ids[0], "pages": [1]}),
    ]
    for label, operation, body in operations:
        endpoint = f"/v1/libraries/{lib}/{operation}"
        raw = client.post(endpoint, json=body)
        compact = client.post(endpoint, params={"view": "compact"}, json=body)
        assert raw.status_code == compact.status_code == 200
        raw, compact = raw.json(), compact.json()
        assert texts(raw) == texts(compact["evidence"])
        raw_tokens = len(tokenizer.encode(evidence_json(raw)))
        compact_tokens = compact["metadata"]["budget"]["returnedTokens"]
        rows.append(
            {
                "operation": label,
                "raw_json_text_tokens": raw_tokens,
                "compact_evidence_text_tokens": compact_tokens,
                "reduction_percent": round(100 * (raw_tokens - compact_tokens) / raw_tokens, 1),
                "attachments": len(compact["attachments"]),
                "all_source_text_and_descriptions_preserved": True,
            }
        )
    client.close()
    return {
        "method": "Local invented PDF/Office fixtures; in-memory services; mocked layout, descriptions and embeddings.",
        "tokenizer": tokenizer.name,
        "serialization": "UTF-8 JSON, ensure_ascii=False, separators=(',', ':')",
        "scope": "Raw response JSON versus compact evidence JSON; excludes attachments/metadata from model text.",
        "actual_cumulative_model_tokens_measured": False,
        "actual_cosmos_ru_measured": False,
        "latency_or_dollar_savings_established": False,
        "results": rows,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(measure()), indent=2))
