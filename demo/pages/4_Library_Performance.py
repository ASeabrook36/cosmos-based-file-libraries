"""Performance workbench for the prototype's shared file libraries."""

import streamlit as st
from common import client, guard

from fli.ingestion import chunking_description

st.title("Library performance")
st.caption("Inventory, ingestion and retrieval measurements for Azure architects and AI developers.")
api = client()
libraries = guard(api.list_libraries, "shared") or []
if not libraries:
    st.info("Create a shared library first.")
    st.stop()
library = st.selectbox(
    "Library",
    libraries,
    format_func=lambda lib: (
        lib["name"]
        + (
            f" · {lib['comparisonMember']} · {lib['ingestionProfile']['extraction']}"
            f" · {chunking_description(lib['ingestionProfile'])}"
            if lib.get("comparisonMember")
            else ""
        )
    ),
)
st.button("Refresh measurements")
data = guard(api.library_metrics, library["id"], "prototype-performance-viewer")
if not data:
    st.stop()
st.json(data["ingestionProfile"])
if data.get("experimentId"):
    st.json(guard(api.get_experiment, data["experimentId"]))
st.caption(
    "Complete-strategy comparisons vary the full pipeline. "
    "Controlled 500-token, zero-overlap pairs share layout extraction "
    "and model settings; "
    "chunk boundaries differ, with hierarchy and visuals disabled. Hosting/orchestration still differ. "
    "Missing counters are unavailable, not zero."
)
st.caption(
    "Chunk and extracted-text token counts are tokenizer estimates; embedding input tokens are provider-reported."
)
inv, summary, history = data["inventory"], data["summary"], data["history"]
inventory_tab, retrieval_tab = st.tabs(["Documents, chunks & embeddings", "Retrieval history"])
with inventory_tab:
    columns = [*st.columns(2), *st.columns(2)]
    for col, label, value in zip(
        columns,
        ["Active documents", "Chunks", "Embedding dimensions", "Measured documents"],
        [inv["activeDocuments"], inv["chunks"], inv["embeddingDimensions"], inv["measuredDocuments"]],
        strict=True,
    ):
        col.metric(label, value if value is not None else "Not recorded")
    st.caption(
        f"Embedding model: {inv['embeddingModel'] or 'Not recorded'}. "
        "Inventory counts current active generations; measured processing totals exclude older uninstrumented files."
    )
    rows = []
    for doc in data["documents"]:
        m = doc["processingMetrics"] or {}
        rows.append(
            {
                "file": doc["fileName"],
                "profile": (doc.get("ingestionProfile") or {}).get("id", "current-v1"),
                "attempt events": len(doc.get("attemptEvents") or []),
                "coverage": str(doc.get("coverage") or {}),
                "status": doc["status"],
                "generation": doc["generation"],
                "chunks": doc["chunkCount"],
                "chunk text tokens": m.get("chunkTokens"),
                "mean chunk tokens": m.get("chunkTokensMean"),
                "min chunk tokens": m.get("chunkTokensMin"),
                "max chunk tokens": m.get("chunkTokensMax"),
                "extracted text tokens": doc["extractedTextTokens"],
                "embedding input tokens": m.get("embeddingTokens"),
                "embedding requests": m.get("embeddingRequests"),
                "embedding time ms": m.get("embeddingMs"),
                "embedding retries": m.get("embeddingRetries"),
                "chunk-write RU": m.get("chunkWriteCharge"),
                "chunk-write time ms": m.get("chunkWriteMs"),
                "processing time ms": m.get("totalMs"),
                "chunking version": doc["chunkingVersion"],
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)
    measured = [d["processingMetrics"] for d in data["documents"] if d["processingMetrics"]]
    if measured:
        cols = [*st.columns(2), *st.columns(2)]
        tokens = [m["embeddingTokens"] for m in measured if m.get("embeddingTokens") is not None]
        charges = [m["chunkWriteCharge"] for m in measured if m.get("chunkWriteCharge") is not None]
        retries = [m["embeddingRetries"] for m in measured if m.get("embeddingRetries") is not None]
        cols[0].metric("Known embedding input tokens", f"{sum(tokens):,}" if tokens else "Unavailable")
        cols[1].metric("Known chunk-write RU", f"{sum(charges):.2f}" if charges else "Unavailable")
        cols[2].metric("Known embedding retries", sum(retries) if retries else "Unavailable")
        st.caption("Known totals cover measured current generations only; blank values mean unavailable, not zero.")
    else:
        st.info(
            "Processing measurements start with newly ingested or replaced files. "
            "Historical token use and write RU are not inferred."
        )
with retrieval_tab:
    if not history["enabled"]:
        st.info(
            "Retrieval history is not enabled for this process. Configure FLI_METRICS_FILE outside the repository, "
            "shared by the local API and worker, then make retrieval calls. Inventory remains available."
        )
    else:
        columns = [*st.columns(2), *st.columns(2)]
        columns[0].metric("Retrieval calls in window", summary["retrievalCalls"])
        columns[1].metric("Reported retrieval RU", f"{summary['requestCharge']:.2f}")
        columns[2].metric(
            "Median retrieval", f"{summary['latencyP50Ms']:.0f} ms" if summary["latencyP50Ms"] is not None else "—"
        )
        columns[3].metric(
            "p95 retrieval", f"{summary['latencyP95Ms']:.0f} ms" if summary["latencyP95Ms"] is not None else "—"
        )
        st.caption(
            f"{summary['searches']} searches · {summary['fullReads']} complete reads · "
            f"{summary['budgetRefusals']} budget refusals · {summary['errors']} errors · "
            f"{summary['distinctAgents']} calling agents · {summary['emptySearches']} empty searches · "
            f"{summary['thinSearches']} thin searches"
        )
        if summary["queryEmbeddingTokensComplete"]:
            st.caption(f"Query embedding input tokens: {summary['queryEmbeddingTokens']:,}")
        if not summary["chargeComplete"]:
            st.warning("Some calls have unreported RU; the displayed charge is a lower bound.")
        st.write("By retrieval operation")
        st.dataframe(summary["byOperation"], hide_index=True, use_container_width=True)
        st.caption(
            f"Observed from {history.get('observedFrom') or 'no events yet'}. "
            f"At most {history.get('eventLimit', 2000)} events from the bounded journal scan; not lifetime usage."
        )
        if history.get("bounded"):
            st.warning(
                "This history is truncated to the configured scan/event window. Summaries cover only those events."
            )
        events = [{k: v for k, v in event.items() if k != "documentId"} for event in reversed(history["events"])]
        st.dataframe(events, hide_index=True, use_container_width=True)
for note in data["notes"]:
    st.caption(note)
st.caption(
    "The local journal is a single-host prototype adapter. "
    "Production should export these metrics to its telemetry backend. "
    "Chat model usage and response time are shown per answer in Chat, "
    "without allocating them artificially to a library."
)
