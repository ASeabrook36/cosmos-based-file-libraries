# Untested at the high end: per-library throughput and library count

*Version 1.1, 2026-10-28: source figures corrected (`evidence.md` E57), Microsoft's status re-checked (E58), and two scale risks added (sections 5 and 6). Version 1.0, 2026-10-24. Records what this project has **not** tested about very large or very busy deployments, so that the design's claims are read with their limits. Nothing here was measured live. It comes from Microsoft's documentation (checked 2026-10-24), the installed `azure-cosmos` 4.17.1 source, this repository's own measurements (`evidence.md`) and arithmetic on them.*

The companion document, `provisioned-throughput.md`, explains why production should run on provisioned throughput. This one assumes it does, because most of the high-end behaviour below exists only on a provisioned account.

## Summary

The data design is not expected to hit a hard wall as libraries, users or content grow. It also has not been tested at the high end. Four questions are open:

1. **A busy library's search ceiling is about one physical partition: 10,000 RU/s.** Its documents can be split across partitions, because the `chunks` key has a document level under the library. That raises the library's storage and write ceilings. It does little for searches: a search must then visit every partition holding the library, so each of them still works on every search. And **the search code may not merge results correctly from a split library.**
2. **How many libraries one container can hold is not limited by Cosmos's partitioning, but may be limited by the vector index.** Each library gets its own vector index (shard). Microsoft documents no limit on the number of shards, and the largest number ever measured is 2,000.
3. **Multi-partition behaviour as a whole is unmeasured** (N4). Every measurement so far ran on one physical partition.
4. **Some of the exact configuration is preview or needs Microsoft's involvement** (N5, N6): sharded DiskANN, filtered DiskANN and the spherical quantizer are public preview (E58), and Microsoft's pages contradict each other on whether vector settings can change.
5. **Whether hybrid search without a partition key fans out as the container grows** (section 5), and **whether small libraries stay cheap to search when there are many of them** (section 6). Both are risks to the claim that a search's cost does not grow with the number of libraries.

None of these is evidence against the design. Each is a question to settle before production.

## Grades

As in `evidence.md`:

- **Vendor:** Microsoft documentation.
- **Measured:** a live run, in this project or the source project.
- **Code:** read from the source of this repository or the installed SDK.
- **Inference:** reasoning from the above, not tested.

## The limits that are not walls · Vendor

From [Service quotas and default limits](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits) and [Partitioning](https://learn.microsoft.com/en-us/azure/cosmos-db/partitioning):

| Limit | Value | What it means here |
|---|---|---|
| Distinct partition-key values | Unlimited | No cap on the number of libraries or documents from partitioning |
| Storage per container | Unlimited | No cap on total content |
| Throughput per container (provisioned) | 1,000,000 RU/s, raised by support ticket | Tens of thousands of searches per second in aggregate, at 15–40 RU each |
| Throughput per physical partition | 10,000 RU/s | **The one that matters** (question 1) |
| Storage per physical partition | 50 GB | About 5 million chunks at 9.62 GB per million (E11; whole abstracts as chunks) |
| Storage per logical partition | 20 GB | A logical partition here is one document of one library; documents are capped at 5,000 chunks (E29) |
| Databases plus containers per account | 500, **cannot be raised** | Why one container per library was never an option (E1). The design uses 3 |
| Accounts per subscription | 250 by default, raisable to 1,000 | Matters only if the platform serves hundreds of separate organizations (`spec.md` §1: one account each) |
| Item size | 2 MB | Chunks are a few KB; the library record holds its tag vocabulary (see question 1's notes) |

## 1. A busy library's throughput ceiling

### What is known · Vendor

- A physical partition serves at most 10,000 RU/s. All replicas of a partition share that budget, so replicas do not add throughput.
- With a hierarchical partition key, Cosmos keeps documents that share the first-level key together on one physical partition. It spreads them across partitions only after splits ([Hierarchical partition keys](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys)). So **a new library starts on one partition**, and its searches and writes share that partition's budget with every other library on it.
- A query that names a library prefix goes "to only the subset of physical partitions that contain the relevant data" (same page).
- A split keeps each *logical* partition whole. Microsoft says splitting does not help when **a single partition-key value** is hot ([Redistribute throughput across partitions](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-redistribute-throughput-across-partitions)). Here a logical partition is one *document* of one library (`/libraryId` → `/documentId`). A busy library is therefore many partition-key values, not one.
- Splits are triggered by data (a partition passing 50 GB) or by throughput: raising a container's RU/s past what its partitions can hold, or using redistribution to give one partition up to 20,000 RU/s. A split takes 4–6 hours.

### What follows · Inference

- **Before any split,** one library's ceiling is one partition: 10,000 RU/s. That is about **250–650 searches per second** on `chunks`, at 15–40 RU per search (the vector query is 8–16 RU, E8, Phase 1; neighbours add one query per result document). Ingestion on the same partition lowers it.
- **After a split that divides the library, writes and storage spread, but searches do not.**
  - *Writes and storage:* each document lives on one partition, so each write goes to one partition, and the library's write ceiling and storage grow with the number of partitions.
  - *Searches:* a search names the library, not a document. It goes to every partition holding part of the library, and each partition runs its own vector search over its part. So every partition still handles every search.
  - *So a split raises the search ceiling only as far as searching part of a library costs less than searching all of it.* For a scan of compressed vectors (`QuantizedFlat`, used on small libraries, E12), cost roughly follows size, so a split could nearly double the ceiling. For a DiskANN graph, cost grows slowly with size: the source project measured about 4 RU at the median for its smallest owner slices and about 22 RU for its largest (p95 7–27 RU), one owner and one type at a time (E3). Those searches were filtered, never partition-key routed (E57). There, a split would add little, and each search would cost more in total. Nothing has measured this.
- **What the `/documentId` level is for:** it keeps every logical partition small (under the 20 GB limit), makes per-document work a single-partition operation, and lets a large library's storage and writes spread across partitions. Without it, a library would be a single logical partition, permanently capped at 20 GB and 10,000 RU/s for everything. It is not a way to multiply a library's search throughput.
- **Writes have the same ceiling.** At about 144 RU per chunk (E10), one partition takes at most about 70 chunks per second. The embedding quota is lower: about 44 chunks per second at 1,000,000 tokens per minute and 381 tokens per chunk. So embedding limits a single library's loading before Cosmos does.
- **For perspective,** 500 searches per second against one library means hundreds of agent turns per second, each with thousands of chat-model tokens: tens of millions of tokens per minute. That is far above a typical Azure OpenAI deployment's quota. The chat model is expected to bind before this ceiling does.

### What is untested

1. **Whether a split divides a library at all.** Microsoft documents that a first-level key "can span multiple physical partitions" after splits caused by storage. It does not say whether a split caused by *throughput*, on a partition dominated by one library, puts the split point inside that library's range.
2. **Whether search returns correctly ranked results from a library on two partitions. This is the main risk.** *Code, `azure-cosmos` 4.17.1:* with a prefix partition key, `__QueryFeed` in `aio/_cosmos_client_connection_async.py` resolves the prefix to a range of hash values, then sends the query to each overlapping physical partition in turn and appends their pages. The SDK fetches a query plan, which is what merges `ORDER BY` results across partitions, only when the service rejects the direct query (`_ProxyQueryExecutionContext` in `_execution_context/aio/execution_dispatcher.py`). This is the same mechanism that left hybrid searches unranked (E27). *Inference:* a vector search on a library spread over two partitions may return partition A's `TOP` results followed by partition B's. `search.py` would then keep partition A's first 8 and drop better matches from B. Nobody would notice, because results would still look plausible.
   - **If confirmed, the fix is a code change, not a rebuild.** Either project the distance (`VectorDistance(…) AS score`) and merge the per-partition lists by it in `CosmosChunkReader.search`, or send vector queries without a partition key (the library still in `WHERE`) so the SDK fetches a query plan, at the cost of an extra round trip (E27 measured that path for hybrid).
   - Until a library passes about 50 GB or a partition is split on purpose, every library sits on one partition and the risk cannot occur. That covers every deployment so far.
3. **What a split costs in the meantime.** Each new partition needs its own copy of the library's vector index. Whether searches scan while it builds (as on a new container, E25), and for how long, is unknown.
4. **The cost and latency of a library on several partitions:** about 2–3 RU per extra partition contacted, by Microsoft's general figure, plus one vector search per partition. Above all, whether a partition's search over its part of the library costs enough less than a search over the whole for a split to raise the search ceiling at all.

### The levers, if a library outgrows one partition · Vendor

| Lever | Status | Effect | Cost |
|---|---|---|---|
| Dynamic autoscale (`enablePerRegionPerPartitionAutoscale`) | Generally available; Microsoft recommends it for every autoscale customer ([Autoscale FAQ](https://learn.microsoft.com/en-us/azure/cosmos-db/autoscale-faq)) | Each partition scales on its own and is billed on its own, so a container can be given a high maximum without paying for idle partitions | Pays for what each partition uses |
| Split the hot partition | Generally available (raise RU/s), or through redistribution (preview) | Spreads a library's documents, and so its writes and storage, over more partitions, if untested items 1 and 2 hold. Raises its search ceiling only as far as per-partition search cost falls (above) | Higher maximum RU/s; autoscale bills only what is used |
| Read regions | Generally available | **The one lever that multiplies a library's search capacity.** Each region has its own provisioned RU/s and serves its own reads, so two regions roughly double what one library can serve | As much again per region. Suits only a library that is genuinely this busy |
| Throughput redistribution | Preview | Moves RU/s onto a hot partition, up to 10,000, without raising the total | None by itself. Afterwards, throughput can only be changed through the same command until reset |
| A dedicated container for one library | Always available | Removes the library from shared partitions. A container keyed differently (for example by `/documentId`) can spread one library over many partitions | Operationally heavy; counts against the 500 limit. Last resort |

### How to test it

On a small provisioned account, after the canary (invariant 11) and after Microsoft has answered N6:

1. Create `chunks` with an autoscale maximum that yields two physical partitions, and load one library with a few tens of thousands of real chunks (E14).
2. Use redistribution to set the library's partition to more than 10,000 RU/s, which forces a split. Confirm from service data (Insights by `PartitionKeyRangeId`, or the partition-key-ranges REST call) that both children hold part of the library.
3. Wait for the vector index to engage (E25). Compare normal searches with exact searches (`VectorDistance(…, true, …)`), counting the partition ranges each response came from (`x-ms-documentdb-partitionkeyrangeid`, captured in `raw_response_hook`). Check that the top 8 match exact search's ranking, not one partition's.
4. Record each partition's request charge per search before and after the split, which shows how far splitting raises the search ceiling. Also record latency and index lag after the split.

Rough cost: tens of dollars, mostly a few hours of elevated throughput while the split runs. It needs spending authorization and a new provisioned account (`provisioned-throughput.md`, "Testing on provisioned throughput").

## 2. How many libraries one container can hold

### What is known

- **Partitioning sets no limit.** Distinct partition-key values are unlimited (Vendor).
- **The vector shard key creates one DiskANN index per library.** Microsoft's [sharded DiskANN announcement](https://devblogs.microsoft.com/cosmosdb/sharded-diskann-focused-vector-search-for-better-performance-and-lower-cost/) recommends sharding by tenant for exactly this kind of isolation and states no limit on the number of shards (Vendor).
- **Measured scale:** 2,003 owners in one container in the source project (E3), all on one physical partition, at one container size. The number of owners was never varied. Phase 4 plans 1,000 libraries.
- Libraries below 1,000 vectors are searched by a scan of their own vectors. Microsoft applies the 1,000-vector minimum before quantized or DiskANN indexes are used, and this project observed `QuantizedFlat` at 1,500 (E12). A small personal library is cheap to search either way.

### What is untested

1. **Per-shard overhead at tens of thousands of libraries:** memory, index-build work and write cost per shard, and whether any of them grow with the number of shards on a partition. If every user of a large organization had a personal agent, one container could hold 10,000–50,000 shards. Not every user is expected to, but the ceiling is unknown.
2. **How quickly a new library's index engages in a container that is already full** (N12). It is measured only for a new container: about 6 minutes (E25). A new library scans until then, which costs more but returns correct results.
3. **The metadata paths that grow with the library count:** the cross-partition library list (`GET /v1/libraries`) and per-library summaries. These are application work, measured in Phase 4 and fixed in Phase 5 (review-response finding 4), not limits of Cosmos.
4. **Hybrid's cost as content grows.** Hybrid's full-text statistics cover the whole container, not the library (E27), so its cost may rise with total content, against the design's premise. Vector-only, the default, is not affected. Phase 4 measures it.

### Why it matters

The shard key is a vector setting. Two of Microsoft's current pages say vector policies and indexes "are immutable after creation" and need a new container ([Indexing policies](https://learn.microsoft.com/en-us/azure/cosmos-db/index-policy), [Hybrid search](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/hybrid-search)). The [Vector search](https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search) page says they can be dropped and added back with new settings. Until Microsoft resolves this, a shard-key limit discovered in production means a new container rebuilt from stored chunks (E16). That makes the shard count the design's most expensive unknown.

### How to settle it

- **Ask Microsoft** for the supported number of vector-index shards per container and per physical partition, together with N6 (below).
- **Test the count, not the size:** about 10,000 tiny libraries of 30–100 real chunks each, measuring write RU per chunk, per-search RU and index lag as the count rises. This is an extension of Phase 4's 1,000.

## 3. Multiple physical partitions in general (N4)

Every measurement so far, in this project and the source project, ran with all data on one physical partition. Prefix routing to a subset of partitions is documented (Vendor), and a small library lives on one partition, so behaviour for small libraries should not change (Inference). The first multi-partition run should confirm three things:

- searches on a small library contact one partition range;
- per-search cost does not change with the number of partitions;
- index engagement and recall are the same as on one partition.

The test for question 1 covers most of this.

## 4. Platform support and documentation

| Item | Status | Before production |
|---|---|---|
| Vector search on hierarchical keys | Microsoft's vector-search, sharded DiskANN and performance-tips pages ask users to email the Cosmos search team "to configure your account" (checked 2026-10-28, E58). The source project reports that the address rejected external mail in August 2026 (N6) | Contact Microsoft, by that address or a support request; apply any account configuration before multi-partition tests, since it may change the results |
| Sharded DiskANN (`vectorIndexShardKey`) | **Public preview** since April 2025, no GA notice (E58). It is what gives each library its own vector index | Ask for the roadmap. No GA alternative keeps per-library indexes in one container |
| Filtered DiskANN | Public preview since May 2025 (E58) | Ask with the above |
| Spherical quantizer | Preview (N5). Product quantization is the generally available fallback, at somewhat lower recall (E6) | Ask for the roadmap |
| float16 vectors | Generally available since November 2025 (E58; N10 corrected). The page notes "some reduction in accuracy may result"; E7 measured none. Two source accounts stopped serving float16 without explanation (E17) | Ask about E17 with the N6 contact |
| Changing vector settings in place | Microsoft's pages contradict each other (question 2) | Ask which is correct; until then treat vector settings as permanent (E16, invariant 10) |
| The partition key | Cannot change, ever (Partitioning page) | None. Its routing is documented and has held in every measurement |

## Questions for Microsoft

One message, sent with the N6 contact:

1. Account configuration for vector search on a container with a hierarchical partition key and `vectorIndexShardKey` (N6).
2. The supported number of vector-index shards per container and per physical partition, and any per-shard overhead.
3. Support status and roadmap for sharded DiskANN, filtered DiskANN and the spherical quantizer, and an explanation of the float16 account failures in E17.
4. Whether vector policies and indexes can be dropped and re-added on an existing container, given the conflicting pages.
5. Whether a throughput-triggered split can divide one first-level key of a hierarchical key, and how the Python SDK is meant to merge `ORDER BY VectorDistance` results when a prefix spans partitions.
6. How a hybrid (`ORDER BY RANK RRF`) query is routed on a hierarchical-key container when no partition key is passed.

## 5. Hybrid cost when the container grows · risk

E56 measured hybrid search with the library in `WHERE` and no partition key, the only shape that ranks from Python, on one library on one physical partition: 138–158 RU per search. It cannot show two things. Full-text statistics are computed over the whole container by default (E27), so the cost may grow with the number of libraries. And without a partition key the SDK's query plan addresses partition-key ranges, so on a container spread over several physical partitions the search may fan out to every one of them. Either would make hybrid cost grow with the organization, not the library. The `Local` statistics scope that is meant to address the first cannot yet be combined with a ranked hybrid query from Python (E56).

The measurement is Phase 4 item "hybrid cost as the container grows" (`build-plan.md`). Vector-only search, the default, is unaffected by the full-text statistics.

## 6. Small libraries under QuantizedFlat at many libraries · risk

Phase 0 saw Cosmos serve a vector search on one 1,500-vector library through `QuantizedFlat` **sharded on `/libraryId`**, at 15 RU (E12). The source project saw something different on another configuration: an index-served `QuantizedFlat` query cost about **5,450 RU whatever its scope**, even under a partition-key filter of about 1,700 documents, on a million-document container **with no shard key** (E57; one read-only investigation, cause not established). Microsoft's performance-tips page suggests under 40 RU for such searches.

Nothing here contradicts Phase 0, because the configurations differ: Phase 0 had a shard key and one library. But with many libraries in one container, whether a small library's `QuantizedFlat` search stays scoped to that library's shard, rather than reading a shared quantized index, is **untested**. If it does not, a small library's search cost would grow with the container's total content, which is the claim this design most depends on. This is recorded as a risk, not a finding. Phase 4 item "QuantizedFlat scoping at many libraries" (`build-plan.md`) measures it.

**Measured 2026-10-29 (E59): scoped.** From 10 to 1,013 libraries and from 25,000 to about 245,000 passages on one physical partition, ten probe libraries of 1,000–5,000 vectors kept their cost (within 1%), documents read and recall, and Cosmos reported the `/libraryId` shard throughout. The same search without its library filter grew from 1,204 to 11,777 RU. Several physical partitions and libraries above 5,000 vectors remain open.

## What would make us reconsider the design

- Microsoft says the hierarchical key combined with a library shard key is not a supported configuration (N6).
- A supported shard count well below the number of libraries an organization needs.
- A stated traffic target for one library above what one partition can serve (about 250–650 searches per second), with no budget for read regions. Splitting is not expected to raise that ceiling much.
- Searches on small libraries that contact unrelated partition ranges, or whose cost grows with the number of libraries (section 6).
- Hybrid search whose cost grows with the number of libraries or physical partitions (section 5); hybrid would then stay an option for small deployments only, and vector-only would remain the default.

Anything else in this document is fixed in code, configuration or capacity settings, without touching the `chunks` container's key or vector settings.
