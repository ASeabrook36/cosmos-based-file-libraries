Authored by Strategist v4.

# Library organization assessment

Date: 2026-10-23 (America/New_York).
Reviewed snapshot: [39632f2](https://github.com/ASeabrook36/cosmos-based-file-libraries/tree/39632f2b1c8f2db7bee65323888228fbba83fee0).
Status: technical consultation and recommendations; no new binding ruling, product decision, production approval, or authorization of spend.

## Judgment

Keep the library-centered data model: three containers, chunks partitioned by libraryId then documentId, and the vector index sharded by libraryId. It fits the stated workload of shared libraries reused across agents and personal libraries bound to one personal agent. The repository supports a credible small-scale implementation; it does not yet establish that this exact configuration is the fastest or cheapest production configuration.

The important transfer from Pragmantic Index is the mechanism: scope a vector search by the body of content it is actually allowed and intended to search. In this application that body is the library. Copying representation-class or residence dimensions into this key would add routing and query fan-out without an established need.

## Basis and verification

Read the canonical Strategist profile v4, firm charter and advisory structure, repository README, CLAUDE.md, spec, build plan (the repository's current phase record), evidence and prior review response. Root AGENTS.md and .github/copilot-instructions.md each returned GitHub API 404; no repository-specific authority envelope is assumed.

Inspected container definitions and the search, document-store, ingestion, chunk-write, embedding and demo-agent paths. Read the committed Phase 0 canary and Phase 1 and Phase 3 filters records. Current snapshot's commit trailer identifies Claude Opus 5.5; this consultation uses a different model family. This is an architecture assessment, not a formal PR approval or complete conformance review.

Verified means inspection of code or committed machine output, not a fresh live execution. No Azure operations, new experiments, local test runs, implementation changes or protocol changes were performed. Pragmantic-origin aggregate measurements were read as reported evidence in docs/evidence.md, not independently reproduced or audited against the original campaign.

## What fits the product

- A shared library's chunks are stored once irrespective of the number of attached agents. Attachments remain in the platform; no per-agent index duplication is needed.
- The libraryId leading partition-key component matches every search's scope. The documentId component keeps document maintenance and neighbour queries localized and allows a large library's chunks to spread across partitions.
- The vector shard key matches that same search scope; this avoids asking a broad graph to find a small library through a filter alone.
- Personal binding is immutable and checked on every search. It is a backstop to platform authorization, not a replacement for it: X-Agent-Id is trustworthy only when a trusted platform supplies it and enforces caller access.
- Blob stores original bytes; Cosmos stores searchable text, metadata and vectors. Explicit scalar indexing and not returning vectors are appropriate cost choices.

The spec currently describes shared libraries as attachable to official agents. Andrew's phrase “assignable to multiple agents” does not independently settle whether personal agents should also be allowed to attach shared libraries. The storage layout can support that behavior; changing the platform's attachment policy is an Andrew-owned product decision, not a reason to change partitioning.

## Qualifications that affect speed, scale and cost

### 1. Library isolation is scoped routing, not dedicated capacity

The README and E3 overstate the mechanism when they promise one physical partition and search cost independent of other libraries. Microsoft documents prefix routing to the subset of physical partitions holding that prefix. A sufficiently large library can span several. Other libraries also consume the container's shared capacity and can affect latency even when their vectors are excluded.

The chunks hierarchy avoids a library-wide 20 GB logical-partition limit; the documents container still uses libraryId alone and retains that per-library metadata-partition limit. This is not a demonstrated present bottleneck. One highly popular shared library can also become a hot scope before storage is large enough to trigger useful distribution.

Recommendation: retain the keys, qualify the claims, and measure routing and latency after physical partition distribution. Do not interpret the planned 1,000-library test on one physical partition as proof of multi-partition behavior.

### 2. A write-concurrency cap does not reserve search capacity

Verified in code: Cosmos writes have a governor shared within one worker process. It limits in-flight operations, not RU/s, and a second worker has its own governor. Search and ingestion use the same configured embedding deployment. Library sessions serialize work within a library, but do not reserve embedding quota or database capacity for interactive searches.

Recommendation: retain sessions for ordering, add explicit ingestion budgets and foreground headroom, and evaluate separate embedding deployments with allocated quota for ingestion and search. Choose the allocation from concurrent-load measurements; no new quota or spend is authorized here. Chunk writes should become bounded streaming batches rather than creating a whole document's vectors, records and waiting tasks at once.

### 3. Metadata maintenance is a more immediate scaling weakness

Verified: refresh_library lists every document after each successful ingest or retag. Loading N documents therefore entails roughly N(N+1)/2 document visits for refresh alone. This known issue is already in the prior review and Phase 5.

Additionally, the library item holds the complete tag vocabulary and author/filter-value maps. Search point-reads that entire growing item for its access check. It therefore cannot be assumed to stay a roughly 1 RU read, and ultimately approaches the Cosmos item-size limit even if tool output caps its displayed vocabulary.

Recommendation: incremental or coalesced summaries, paginated lists, and bounded/paged facet catalogs separated from the small binding/status record. Keep filterable metadata on chunks; the measured retag cost is a conscious tradeoff for filtering inside retrieval.

### 4. Multi-library turns add application overhead

Verified in the demo: tool definitions are fetched per attached library on each turn, tool calls run sequentially, and each index search embeds its query again. This is acceptable for a few libraries and does not justify changing the keys.

Recommendation when measuring real multi-library turns: parallel independent calls with bounded concurrency, reuse identical query embeddings, and cache tool definitions with explicit invalidation. Revalidate live attachments before execution; a cached tool definition is not authorization. Many-library tool selection and cross-library answer quality remain separate from database scalability.

### 5. Exact configuration and answer quality still need workload evidence

The raw Phase 0 canary measured 1,500 vectors, approximately 143.5 RU per chunk written, approximately 15 RU per indexed vector query, and about 369 seconds until the routed index engaged. Its index-utilization report named QuantizedFlat. That is evidence of useful indexed retrieval, not observed DiskANN graph traversal at large scale.

Phase 3's committed filters record reports approximately 4–15 RU per filtered API search on its small corpus. These numbers are not a production cost model. Earlier Phase 1 and demo charges omitted some point reads; the repository documents that limitation.

E27 reports approximately 128–191 RU for correctly ranked hybrid searches versus 8–16 for vector queries on the measured library. Hybrid full-text statistics span the container, so the favorable per-library scaling expectation must not be generalized to that path.

Recommendation: retain vector search as the current default. Before calling the design optimized, measure the exact layout on small personal and large shared libraries, realistic questions with known answers, selective filters, simultaneous uploads and searches, and increasing physical partition count. Report complete API RU, p95 latency, throttling, freshness, retrieval quality and agent-turn costs. These are recommended comparisons, not newly imposed acceptance thresholds. The existing 3,072 dimensions and full-text index are defensible inherited choices, but their incremental value and cost have not been established on this workload.

## Current documentation discrepancies

Microsoft's vector-search documentation now explicitly lists float16 as supported. Evidence N10's claim that it is absent from the documented types is stale. This resolves that documentation gap; it does not validate every measured query-path behavior or the spherical configuration's production support status.

The same page now describes dropping and re-adding vector policies/indexes with new settings. The repository's blanket “every vector change requires a new container” rule is more restrictive than current documentation. A new container remains a prudent controlled migration strategy; any less disruptive migration requires a separate compatibility check, not an untested edit to the live account.

Microsoft still asks users of vector search with hierarchical partition keys to contact the Cosmos team for account configuration. That remains applicable. Cross-client delete visibility, Entra caller authentication, purge recovery and backup/restore are existing production follow-ups, not evidence against the basic library model.

## Recommended next step

Use Phase 4 as the next evidence-producing step, adding concurrent ingestion and foreground search to the before/after library-count comparison. Preserve the library layout and direct optimization effort toward measured overhead and resource contention. No alternative partitioning scheme is currently justified.

## Sources

Repository sources at the reviewed snapshot: [spec](../spec.md), [build plan](../build-plan.md), [evidence](../evidence.md), [prior review response](../review-response.md), [chunks policy](../../cosmos/chunks.container.json), [search](../../src/fli/search.py), [worker handlers](../../src/fli/worker/handlers.py), [store](../../src/fli/store.py), [demo agent](../../src/fli_demo/agent.py), and [records](../../records/).

Microsoft documentation checked 2026-10-23:
- [Hierarchical partition keys](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys).
- [Hierarchical partition keys FAQ](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys-faq).
- [Vector search](https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search).
- [Sharded DiskANN](https://devblogs.microsoft.com/cosmosdb/sharded-diskann-focused-vector-search-for-better-performance-and-lower-cost/).
