Authored by Strategist v4.

# Library organization assessment

Date: 2026-10-23 (America/New_York). Updated 2026-10-24 with hybrid advice and the requested evidence plan.
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

## Follow-up: what hybrid would earn here

Added 2026-10-24 at Andrew's request. This section records advice and a concrete evidence plan; it changes no accepted protocol, phase exit check, production configuration or spending authority.

Hybrid combines semantic retrieval with lexical ranking. Its credible benefit here is finding distinctive names, product codes, identifiers, rare jargon and explicit wording that embeddings can overlook. Structured metadata filtering already works with vector search; dates, tags, authors and library isolation do not require hybrid. Lexical ranking of body text is also different from a guaranteed literal identifier lookup: tokenization and punctuation handling must be checked on the actual identifiers.

Recommendation: use vector-only search for the structural proof. Include exact-term questions in the quality set below. Do not build a hybrid optimization campaign merely to “cover more ground.” If representative vector searches miss necessary evidence, first inspect extraction, chunking, metadata exclusions and query formulation. If the remaining misses are lexical, make a small paired comparison of the existing hybrid path against vector-only on those cases plus ordinary semantic questions. Record rescued answers, regressions, complete RU, latency and full-text index write/storage overhead. A targeted lexical lookup may suffice for a specific need; its implementation is not commissioned here. Hybrid becomes worth adopting only if its measured benefit justifies its cost for the intended workload.

Microsoft's [hybrid overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview) explains the semantic/lexical distinction and exact-term use cases. Its Azure AI Search relevance claims do not establish quality or economics for this Cosmos implementation.

The current build plan already includes hybrid measurements. Deferring those is a recommendation for Andrew's scope decision, not a silent removal of an existing phase requirement. Leave the existing code and full-text index unchanged during the first structural comparison, so it measures the actual configuration.

## Concrete next evidence: three questions

Extend the existing Phase 4 work rather than start a separate broad benchmark. The table specifies observations needed to answer Andrew's questions; the sequence below keeps the work bounded. Proposed counts are practical starting samples, not scientific acceptance thresholds.

| Question | Concrete comparison | What the result establishes |
|---|---|---|
| Does library-scoped retrieval remain efficient as the container grows and spans physical partitions? | Keep selected personal and shared anchor libraries, their contents and questions fixed. Replay the same searches at 10, 100 and 1,000 populated libraries. Then, in a separately costed multi-partition run, repeat after verifying actual physical distribution. Record which partition ranges each query contacts. | Whether unrelated library growth adds query work; whether routing stays confined to the ranges holding the named library. Library count alone cannot establish multi-partition behavior. |
| Does it work for small personal and large, popular shared libraries? | Use the existing 30–150-chunk personal sizes and shared scopes around 10k, 30k and 100k chunks. Hold a low-rate anchor workload steady, then separately increase shared-library search traffic and add uploads, first into another library and then into the searched library. | Cost versus scope size, saturation and noisy-neighbor behavior; whether latency loss comes from shared capacity, embedding quota, index freshness or an unexpectedly broad query path. |
| Does the exact vector configuration retrieve useful evidence at those sizes? | On stable, indexed corpora, compare indexed top-10 with forced exact vector top-10 using identical queries, converted vectors and filters. Separately score whether the default API top-8 contains the annotated passages needed to answer 50–100 realistic questions. | Approximation/quantization behavior versus the stored-vector reference, and actual evidence-finding quality. High agreement with exact vector search alone does not establish useful answers. |

### Execution sequence and records

1. **Prepare one reproducible runner and manifest.** Extend the planned `scripts/scale_demo.py`; do not implement application features first. Record producing commit, SDK version, exported actual container definitions, capacity mode and configured throughput, observed physical partition count, worker/API instance counts, embedding deployment/quota and search options. Use real permission-cleared documents and embeddings. Persist corpus hashes, library sizes and query-set version; no random vectors or filler text. If material is reused across libraries for load, label that reuse and keep the quality set representative.

2. **Choose anchor libraries and the quality questions before measuring.** Include small personal and each shared-library size, ordinary semantic paraphrases, names/identifiers from the real documents, and selective metadata filters. Annotate supporting document/passage locations and expected no-answer cases. Reuse the same questions in each applicable comparison, and report personal/shared and semantic/exact-term results separately. Keep a few wrong-agent requests to confirm isolation while scaling.

3. **Run the inexpensive existing-layout comparison first.** Load the 10 → 100 → 1,000 library milestones. Keep anchor content unchanged. At each milestone wait until observed index use and RU settle; record the index type rather than assume DiskANN graph traversal. Replay about 20 fixed questions per anchor five times, interleaving libraries and reporting variation, with warm-up separated. Measure a fresh library's time from write completion to indexed retrieval in the populated container separately. A new library may initially scan; do not mix that period into steady-state cost.

4. **Separate database work from API and worker overhead.** Replay cached query vectors directly against Cosmos as a diagnostic, then the normal authenticated API searches. Record vector-query RU/time, complete API RU, embedding time/throttles, document reads and optional neighbor costs. With the baseline held fixed, ramp shared-library request rate in recorded steps, then run the upload comparisons. Record p50/p95 end-to-end latency, achieved request rate, errors, 429s/retries, ingestion rate, index lag and worker memory. Meter refresh/list work and library-item bytes separately. If performance degrades, first repeat the affected case with ingestion paused or reduced, changing one lever at a time. A fresh-index delay or embedding bottleneck does not by itself indict the partition key.

5. **Make the multi-partition step explicit and bounded.** Before provisioning or loading more, prepare a cost/resource estimate, proposed sizes and means of verifying physical distribution. Obtain any required authorization under the existing infrastructure/spend boundaries. Use service-reported physical-partition information and per-query diagnostics; do not infer distribution from library count or a rough GB-per-chunk estimate. First test a container with multiple partitions while anchors remain small. Testing one library spread over several partitions is a distinct case: demonstrate it if the target workload and authorized envelope support it; otherwise report it unmeasured. For isolated routing comparisons retain comparable capacity headroom, identify partition splits, and separate transient index rebuilding from settled performance. Redact sensitive identifiers in committed diagnostics.

6. **Measure quality on a quiescent snapshot.** Finish ingestion/purges and use ready documents with stable active generations. Forced exact and indexed queries must use the same float16 conversion, filters, candidate scope and TOP. Account for ties and scopes with fewer than ten eligible chunks. Record recall@10 across repeats. That exact reference uses the same stored vectors, so it cannot establish absence of float16 loss against float32 or suitability of the embedding model. For the annotated questions separately report answer-support hit rate at API top-8, missing evidence, spurious results on no-answer questions and filter exclusions. Any optional end-to-end answer evaluation is reported separately from retrieval quality. Diagnose failures before introducing another layout or a hybrid matrix.

7. **Commit a compact result with the raw evidence.** Use `records/` for machine output and a concise linked report alongside the build plan. For each question state: measured sizes/load/topology, observed result, remaining uncertainty, and whether the next fix concerns workers/API, supporting metadata records, capacity or the chunk partition/vector-index design. Report serverless RU as RU, and model production autoscale/storage/AI costs separately; do not convert RU into a production price using serverless unit rates. Record each live run's actual resource use and completion state.

### Interpretation and decision boundaries

The three implementation concerns do not currently require changing `libraryId → documentId` or the `libraryId` vector shard key. Worker pacing/batching, incremental summaries and parallel API orchestration can be changed around that layout. Moving growing facet catalogs out of the hot library record is a supporting metadata adjustment, not a chunk-container rebuild.

Evidence that would justify reopening the foundational design includes settled queries contacting unrelated ranges, scans or index-quality failures at intended large-library sizes, or a popular library's capacity ceiling remaining inadequate after measured capacity and workload controls. Diagnose any failure against actual routing, query shape, SDK/service behavior and declared support before concluding that the keys are wrong.

The measurements should expose performance curves and limitations. Andrew retains the decision on required scale, acceptable latency/quality, cost and any new live-resource commitment. A 1,000-library demonstration establishes only the tested topology and traffic; it does not certify arbitrary production scale. No tests have been executed by this documentation update.

## Sources

Repository sources at the reviewed snapshot: [spec](../spec.md), [build plan](../build-plan.md), [evidence](../evidence.md), [prior review response](../review-response.md), [chunks policy](../../cosmos/chunks.container.json), [search](../../src/fli/search.py), [worker handlers](../../src/fli/worker/handlers.py), [store](../../src/fli/store.py), [demo agent](../../src/fli_demo/agent.py), and [records](../../records/).

Microsoft documentation checked 2026-10-23:
- [Hierarchical partition keys](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys).
- [Hierarchical partition keys FAQ](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys-faq).
- [Vector search](https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search).
- [Sharded DiskANN](https://devblogs.microsoft.com/cosmosdb/sharded-diskann-focused-vector-search-for-better-performance-and-lower-cost/).
