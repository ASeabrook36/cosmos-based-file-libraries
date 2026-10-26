"""Optional content-free prototype metrics journal, outside the retrieval data model.

One host's API/worker can share this append-only JSONL sink. Production deployments should
export the same fields to their telemetry backend; this is not a distributed analytics DB.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("fli.metrics")
OPERATIONS = {"search", "read", "discover", "ingest", "error"}
FIELDS = {
    "agentId",
    "documentId",
    "generation",
    "totalMs",
    "cosmosMs",
    "requestCharge",
    "resultCount",
    "disposition",
    "estimatedTokens",
    "contentReturned",
    "chunkCount",
    "chunkTokens",
    "embeddingTokens",
    "embeddingMs",
    "embeddingRequests",
    "embeddingRetries",
    "embeddingThrottles",
    "chunkWriteMs",
    "chunkWriteCharge",
    "status",
    "failedOperation",
}
MAX_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 2000


def journal() -> Path | None:
    value = os.environ.get("FLI_METRICS_FILE")
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("FLI_METRICS_FILE must be outside the reference repository")
    return path


def record(operation: str, library_id: str, **values: Any) -> None:
    """Never let unavailable observability fail a retrieval or ingestion."""
    try:
        path = journal()
        if path is None:
            return
        if operation not in OPERATIONS:
            raise ValueError("unknown metrics operation")
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "libraryId": library_id,
            **{k: v for k, v in values.items() if k in FIELDS and isinstance(v, (str, int, float, bool, type(None)))},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, (json.dumps(event) + "\n").encode())
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        log.warning("metrics_unavailable")  # no paths, exception bodies, source text or credentials


def read_events(library_id: str) -> dict[str, Any]:
    path = journal()
    if path is None:
        return {"enabled": False, "events": [], "bounded": False}
    if not path.exists():
        return {"enabled": True, "events": [], "bounded": False}
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        offset = max(0, size - MAX_BYTES)
        stream.seek(offset)
        if offset:
            stream.readline()  # discard a partial line
        data = stream.read(MAX_BYTES)
    events = []
    for line in data.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue  # a process may still be completing its final append
        if isinstance(event, dict) and event.get("libraryId") == library_id and event.get("operation") in OPERATIONS:
            events.append({k: v for k, v in event.items() if k in FIELDS | {"timestamp", "operation"}})
    bounded = bool(offset) or len(events) > MAX_EVENTS
    events = events[-MAX_EVENTS:]
    return {
        "enabled": True,
        "events": events,
        "bounded": bounded,
        "observedFrom": events[0]["timestamp"] if events else None,
        "eventLimit": MAX_EVENTS,
        "scanByteLimit": MAX_BYTES,
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 1)


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    retrieval = [e for e in events if e["operation"] in {"search", "read", "discover"}]
    searches = [e for e in retrieval if e["operation"] == "search"]
    errors = [e for e in events if e["operation"] == "error"]
    agents = sorted({e["agentId"] for e in retrieval if e.get("agentId")})
    return {
        "retrievalCalls": len(retrieval),
        "searches": len(searches),
        "fullReads": sum(e["operation"] == "read" and e.get("contentReturned") is True for e in retrieval),
        "budgetRefusals": sum(e.get("disposition") == "budget_exceeded" for e in retrieval),
        "errors": len(errors),
        "distinctAgents": len(agents),
        "requestCharge": round(sum(e.get("requestCharge") or 0 for e in retrieval), 2),
        "chargeComplete": all(e.get("requestCharge") is not None for e in retrieval) and not errors,
        "latencyP50Ms": percentile([e["totalMs"] for e in retrieval if e.get("totalMs") is not None], 0.5),
        "latencyP95Ms": percentile([e["totalMs"] for e in retrieval if e.get("totalMs") is not None], 0.95),
        "emptySearches": sum(e.get("disposition") == "empty" for e in searches),
        "thinSearches": sum(e.get("disposition") == "thin" for e in searches),
        "queryEmbeddingTokens": sum(e.get("embeddingTokens") or 0 for e in searches),
        "queryEmbeddingTokensComplete": all(e.get("embeddingTokens") is not None for e in searches),
        "byOperation": [
            {
                "operation": operation,
                "calls": len(rows),
                "requestCharge": round(sum(e.get("requestCharge") or 0 for e in rows), 2),
                "latencyP50Ms": percentile([e["totalMs"] for e in rows if e.get("totalMs") is not None], 0.5),
                "latencyP95Ms": percentile([e["totalMs"] for e in rows if e.get("totalMs") is not None], 0.95),
            }
            for operation in ("search", "read", "discover")
            if (rows := [e for e in retrieval if e["operation"] == operation])
        ],
    }
