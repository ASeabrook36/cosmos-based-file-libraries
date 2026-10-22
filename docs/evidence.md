# Evidence: why the design is what it is

*Version 0.2.*

Most decisions in `spec.md` rest on measurements from earlier work on these Cosmos features, made on live Azure in 2026. That work built a retrieval index in which each owner (a person) had their own body of content, and every search named whose content to search. That is structurally this project's problem: an owner there plays the role a library plays here. The numbers are recorded here so this repository stands on its own.

**Grades**

- **Measured:** from runs against live Cosmos.
- **Vendor:** Microsoft documentation or a Microsoft engineering response.
- **Inference:** reasoning from measured or documented behaviour, not itself tested.

**About the source measurements**

- Corpus: real text (public reference abstracts) embedded with `text-embedding-3-large` at 3,072 dimensions. These were not workplace documents, and nothing measured image descriptions.
- Each lever was adopted on a single build.
- Unless stated otherwise, the data sat on one physical partition of a serverless account.

## Decisions

### E1. A library is a value in the data, not a service object · Vendor

Azure AI Search allows 50 indexes per service on S1, 200 on S2 and S3, and 1,000 per partition (3,000 per service) on S3 HD. One index per library therefore caps the number of libraries.

Cosmos has the same kind of limit for its equivalent object: 500 databases plus containers per account, which Microsoft says cannot be increased. A container per library would move the ceiling, not remove it. Values stored on records are not counted against any quota.

Sources: [Azure AI Search service limits](https://learn.microsoft.com/en-us/azure/search/search-limits-quotas-capacity); [Azure Cosmos DB service quotas](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits).

### E2. Shard the vector index on the field every search names · Measured

A vector index shard key (`vectorIndexShardKey`) makes Cosmos build a separate DiskANN graph per value, so a search that names the value searches only that graph.

On the same personal-content corpus, a vector index sharded by document type only was compared with one sharded by document type plus owner. Across five traffic mixes, the owner-sharded layout was faster and much cheaper, for a little recall:

| | Type only | Type + owner |
|---|---|---|
| p95 server latency per search (mix-weighted) | 143–295 ms | 97–127 ms |
| p95 cost per search (mix-weighted) | 407–1,063 RU | 54–69 RU |
| Recall@10 against exact search, single-owner searches | 0.9923 | 0.9907 |

Recall for the owner-sharded layout was at most 0.0017 below type-only on every single-owner search kind.

**This project shards on library alone, not type plus library. That is an inference** (Not proven, N2).

### E3. Many owners in one container · Measured

The corpus held about 600,000 chunks across 2,000 owners in one container, sized the way personal content is:

- smallest 43 chunks, median 95, 90th percentile 539, largest 4,957;
- three owners were enlarged to 10,000, 30,000 and 100,000 chunks.

That is close to an organization with 2,000 personal agents plus a few large shared libraries. E2's results come from this corpus.

Because each search names one library, and the library is both the first partition-key level and the shard key, a search reaches one partition and one graph. Its cost tracks the size of that library, not the number of libraries. That last statement is an inference from the design; the measurement was one container on one physical partition.

### E4. One query per library, never an `IN` list across libraries · Measured

One query with an `IN` list over nine shard values cost 71,699 RU. The same search as nine separate queries, one per value, cost 225 RU in total: about 300 times cheaper. Cosmos scanned instead of routing to the shard graphs. This was a single diagnostic, not a claim about every future optimizer.

### E5. The library is always supplied by the server · Measured

On a sharded index, a query that omitted the shard value scanned the whole container: about 7,812 RU, against about 35 RU with it, roughly 220 times. The figure was the same on synthetic and real-embedding corpora. In this project the agent never supplies the library: the platform binds it to the tool, and the index puts it in every query.

### E6. Spherical quantizer, not product · Measured (preview feature)

- Recall was about 0.4 points higher than with product quantization, at equal cost.
- Results churned about half as much on repeated identical searches: 4.5–4.9% of queries changed their top 10, against 8.0–11.5%.
- **Small scopes:** where a scope held only 3–10 relevant documents, the product-quantized build returned 2–9 of them, and the spherical build returned every one. A product full of small personal libraries is exactly this case.
- Spherical is a public-preview setting. Product quantization is generally available and is the fallback.

### E7. float16 vector storage · Measured

Against otherwise identical float32 builds:

- storage was 46% lower;
- recall@10 was equal: 0.980 against 0.980 on the main search kind, within 0.001 on the others;
- total query cost was about 41% lower.

These builds were queried with the E8 query path.

### E8. Query the float16 index with a float16 query vector · Measured

Declaring the query vector as `float32` against a float16 index made Cosmos scan instead of using the index: about 4,789 RU per query, reading the whole population.

The fix:

1. Round the query vector to float16.
2. Set values smaller in magnitude than 2⁻¹⁴ (float16's subnormal range) to 0. The service returns no distance for a float16 query that contains one.
3. Declare `{'dataType': 'float16'}`.

With that change, the same searches cost about 25 RU and read about 0.06% of the population.

An earlier rule said to declare `float32` for the query vector, because omitting the declaration returned no scores. That rule is wrong for float16 indexes. `inherited/query.py` implements the correct path and refuses the wrong one.

### E9. `indexingSearchListSize` 300 · Measured (narrowly)

This is a build-time setting for how widely DiskANN explores while building its graph. At 300 instead of the default 100:

- recall rose by 0.0086, an interval that straddles a 0.01 decision bar;
- no query-cost penalty was measured.

It was measured with the product quantizer only, never with spherical or float16. A build at 500 was unstable and is not used.

### E10. Writes are the expensive side; pace them · Measured

- About 91 RU per chunk written on a DiskANN container, including about 24% for the full-text index. That is about $23 per million chunks on serverless.
- One serverless container sustained about 22 chunks per second. It capped near 1,785 RU/s, about 36% of the documented per-partition figure, and reached that cap at about two concurrent writers.
- During bulk loads, about half of all requests were throttled (HTTP 429).
- Searches share that ceiling. Hence the per-library queue lanes and the write caps in `spec.md` §7.

### E11. Storage per million chunks · Measured

About 17.9 GB per million chunks at float32, with text and the full-text index included. Applying E7's −46% gives about 9.7 GB at float16. That combined figure is arithmetic, not a measurement.

### E12. When the vector index is used · Measured

- Searches routed by shard value used the DiskANN index at every shard size tested, from 201 chunks up.
- Searches filtered on an ordinary field switched from scanning to using the index somewhere between 660 and 1,525 matching documents.
- Smaller scopes are scanned. E6's small-scope misses imply those scans compare compressed vectors rather than full-precision ones; this is an inference.
- Microsoft documents that DiskANN needs at least 1,000 vectors before the index is used. The shard-routed result suggests that applies per container rather than per shard, which is not documented.

### E13. Leave `filterPriority` at its default · Measured

On the type-only layout, setting `filterPriority` to 0.5 or 1.0 dropped recall to 0.83 and 0.82. On the owner-sharded layout it was harmless. It has no measured benefit here.

### E14. Results vary slightly between identical searches · Vendor and measured

Each Cosmos replica builds its own DiskANN graph, and any replica may answer a query. Microsoft's DiskANN team confirmed this behaviour, gave about 95% recall as the working target, and Microsoft's vector-search documentation now describes it.

Two consequences for testing:

- Tests must measure recall against an exact-search reference, never assert fixed result lists.
- Randomly generated vectors are a worst case for every vector index and are not valid test data. Use real text and real embeddings.

Source: [Vector search in Azure Cosmos DB for NoSQL](https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search).

### E15. Explicit indexing; partition paths included; never `/id/?` · Measured and Vendor

- By default Cosmos indexes every field, and the index can grow larger than the data.
- With the root excluded, partition-key paths are not indexed unless listed, and queries on them scan.
- A shard-key field must also be range-indexed, or any non-vector query filtering on it scans.
- An indexing policy that lists `/id/?` is accepted at create and then fails asynchronously: the container is never created, and nothing reports it.

Source: [Indexing policies in Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/index-policy).

### E16. Vector settings are permanent · Vendor

None of these can be changed in place:

- dimensions, data type and distance function;
- index type and quantizer;
- build settings;
- shard key.

A change means a new container plus a rebuild. This is why chunk text and original files are always kept.

Source: [Vector search in Azure Cosmos DB for NoSQL](https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search).

### E17. Prove each new container works at the data plane · Measured

An account that had accepted and served a float16 vector container later entered a state where its data plane could no longer read the stored policy. Reads, writes and even plain queries failed, while a new account in the same region worked.

A successful deployment is therefore not evidence of a working index. Every new container gets a write, read, vector-query and hybrid-query canary before use, and replacing an account is a normal recovery step.

### E18. Tags are filters, never keys · Inference from vendor rules

Partition keys cannot change after a record is written, and users re-tag documents. The earlier work kept every mutable or many-valued attribute out of its keys for the same reason. Here, tags are copied onto chunks and range-indexed instead.

### E19. Three separate time fields · Inference from design work

"Documents about Q4 2025" means the period a document covers, which is often not when it was written or uploaded. The earlier work found two problems:

- substituting one meaning of time for another silently drops the right documents;
- bulk-migrated files share meaningless creation dates.

Hence `period`, `authoredAt` and `uploadedAt` are distinct fields and filters, plus the `authoredAtSuspect` flag.

### E20. Searching across many libraries at once is a different design · Measured

On a layout sharded by owner, sweeping everything without naming owners was slow: p95 of 6.1–15.2 seconds on traffic mixes that included sweeps, against 68–153 ms on a type-sharded layout. Searching up to five named owners in one request measured 87 ms and 31 RU; more than five was untested.

This project avoids the problem: agents choose libraries by description and search each through its own tool. Organization-wide search would need a second container.

### E21. Hybrid search runs; its quality is unmeasured · Measured (execution only)

Hybrid queries (`ORDER BY RANK RRF(VectorDistance(…), FullTextScore(…))`) executed successfully on a live account. RRF (Reciprocal Rank Fusion) merges the vector and keyword result lists by rank position rather than by score.

Their ranking quality was never measured. The local Cosmos emulator rejects `FullTextScore`, so hybrid can only be tested against a real account.

### E22. Capacity mode follows traffic shape · Measured and Vendor

- **Serverless** suits intermittent traffic and costs nothing idle. Its ceiling cannot be raised, it carries an SLO rather than an SLA, and it runs in a single region.
- **Provisioned autoscale** suits sustained traffic that needs predictable performance.
- An account converts from serverless to provisioned one way only.
- Serverless measurements are floors, not guarantees.

Source: [Choose between provisioned throughput and serverless](https://learn.microsoft.com/en-us/azure/cosmos-db/throughput-serverless).

### E23. Keep no mutable copy of permissions · Design principle

The index stores no permissions, and the system of record is checked live. A stale index can then cause a miss, but never a disclosure.

This project applies the principle to agent–library attachments. The platform owns them, and the index holds only the immutable personal-library binding, which cannot go stale.

## Not proven

These are the honest limits of the evidence. Phase 0 and Phase 4 of `build-plan.md` are designed to close the first ones.

- **N1. This exact configuration has never been built.** float16, spherical, `indexingSearchListSize` 300, a shard key and hybrid search have each been measured, but never together. The Phase 0 canary is the first build.
- **N2. Library-only sharding is inferred.** The measurements cover type plus owner, not owner alone. Library alone means one query per search instead of one per document type. Small libraries are scanned either way (E12), so the open question is large shared libraries (10,000–100,000+ chunks). A comparison run on such libraries settles it.
- **N3. Hybrid ranking quality is unmeasured** (E21).
- **N4. One physical partition only.** Everything was measured with the data on a single physical partition. A container spanning many partitions, at roughly 5 million chunks and up, is unmeasured. Each search should still reach one partition, since the library leads the key.
- **N5. Spherical is preview** (E6). Product quantization is the GA fallback, at somewhat lower recall.
- **N6. Microsoft asks to be contacted.** Microsoft's documentation tells users of vector search on containers with hierarchical partition keys to contact the Cosmos DB team so the account can be configured. Treat Microsoft's confirmation as a production prerequisite.
- **N7. Not workplace content.** No measurement used workplace documents, spreadsheets, emails or image descriptions. The demo corpus (`build-plan.md`) should include all of them.
- **N8. float16 write cost was not measured separately.** The 91 RU per chunk figure is from float32 builds.
- **N9. Proposal quality is untested.** Nothing measures how good AI-proposed descriptions and tags are; the review-and-accept step exists for that reason.
