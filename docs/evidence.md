# Evidence: why the design is what it is

*Version 1.5, 2026-10-26: E39 records immutable publication, vision checkpoints and chart/OCR hardening with real Azure fault checks. Version 1.4, 2026-10-23: E32 records the spelling fix running live. Version 1.3, 2026-10-23: E32 records the response to vocabulary drift (spellings mapped at parse time). Version 1.2, 2026-10-23: E33 (an unparseable file is not called encrypted). Version 1.1, 2026-10-23: E32 (retag cost, proposal accuracy and vocabulary drift, measured); E31 observed live; N14 narrowed. Version 1.0, 2026-10-23: E31 (proposals never overwrite or fail a document); N14. Version 0.9, 2026-10-23: E28 extended to metadata updates (`retag`). Version 0.8, 2026-10-23: E30 records the Phase 3 live check, which observed underfill reaching an empty result. Version 0.7, 2026-10-23: E28 extended to ingest (a replacement's write may not be visible yet); E30 (replacement: one original per generation, a generation number never reused, underfill reported). Version 0.6, 2026-10-23: E28 (deletion must be retryable, and the worker must not trust its first read); E29 (bound what one document can cost); N13. Version 0.5, 2026-10-22: E27 (hybrid search routed by partition key does not rank); E21 and E24 corrected. Version 0.4: E26 (search visibility). Version 0.3: Phase 0 measurements added.*

Most decisions in `spec.md` rest on measurements from earlier work on these Cosmos features, made on live Azure in 2026. That work built a retrieval index in which each owner (a person) had their own body of content, and every search named whose content to search. That is structurally this project's problem: an owner there plays the role a library plays here. The numbers are recorded here so this repository stands on its own.

**Grades**

- **Measured:** from runs against live Cosmos.
- **Vendor:** Microsoft documentation or a Microsoft engineering response.
- **Inference:** reasoning from measured or documented behaviour, not itself tested.

**About the source measurements**

- Corpus: real text (public reference abstracts) embedded with `text-embedding-3-large` at 3,072 dimensions. These were not workplace documents, and nothing measured image descriptions.
- Each lever was adopted on a single build.
- Unless stated otherwise, the data sat on one physical partition of a serverless account.

**About the Phase 0 measurements**

Entries marked *Phase 0* come from this project's own canary (`scripts/canary.py`), run on 2026-10-22 against a freshly deployed serverless account with exactly the `cosmos/*.json` containers. The records are `records/canary-20261022T185556Z.json` (failed: it searched before the index engaged) and `records/canary-20261022T201837Z.json` (passed).

- Corpus: two public company annual reports (PDF), 1,500 chunks and 571,623 tokens, embedded with `text-embedding-3-large` at 3,072 dimensions.
- One library: the whole container was 1,500 vectors.
- Queries: 20 vector-only and 20 hybrid searches, each a real sentence taken from the corpus.

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

*Phase 0* confirmed it on this configuration. With the index engaged, the float16 path cost 14.9–15.5 RU and read 50 of 1,500 chunks. The same search declaring `float32` still read all 1,500 at 113.8 RU, the cost of brute force.

An earlier rule said to declare `float32` for the query vector, because omitting the declaration returned no scores. That rule is wrong for float16 indexes. `src/fli/query.py` implements the correct path and refuses the wrong one.

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

*Phase 0*, on float16 vectors with the full-text index:

- **About 144 RU per chunk written** (143.5 averaged over 1,499 upserts), not 91. That is about $36 per million chunks on serverless.
- **Two concurrent writers** sustained 26.7 chunks per second with no throttling.
- **Eight concurrent writers** were throttled hard enough to exhaust the SDK's own 429 retries (sub-status 3200) within about 100 writes. The SDK's retries are not enough on their own; the writer must cap concurrency for the whole container and retry 429s itself, honouring `x-ms-retry-after-ms`.

### E11. Storage per million chunks · Measured

About 17.9 GB per million chunks at float32, with text and the full-text index included. Applying E7's −46% gives about 9.7 GB at float16. That combined figure is arithmetic, not a measurement.

### E12. When the vector index is used · Measured

- Searches routed by shard value used the DiskANN index at every shard size tested, from 201 chunks up.
- Searches filtered on an ordinary field switched from scanning to using the index somewhere between 660 and 1,525 matching documents.
- Smaller scopes are scanned. E6's small-scope misses imply those scans compare compressed vectors rather than full-precision ones; this is an inference.
- Microsoft documents that DiskANN needs at least 1,000 vectors before the index is used. The shard-routed result suggests that applies per container rather than per shard, which is not documented.

*Phase 0*: with 1,500 vectors in one library, Cosmos's index-utilization report named the vector index it used as **`QuantizedFlat`, sharded on `/libraryId`**, not a DiskANN graph traversal. A search read 50 chunks for 15 RU, against 114 RU for brute force, with recall@10 of 1.0 against exact search (19 of 20 top-10 lists identical in order). This confirms the inference above: small scopes compare compressed vectors rather than walk the graph. At what library size Cosmos switches to the DiskANN graph is not yet measured (N11).

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

Originals are therefore rebuild-critical. Blob soft delete keeps a purged original recoverable for 7 days, as Cosmos's continuous backup does for records, so the two can be restored together. The restore drill is Phase 5.

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

*Phase 0*: with the library as partition-key prefix, each hybrid search came back in a single HTTP response, charged 3.91 RU, retrieved 10 documents and reported no vector index used. That held for all 20 queries, whatever their terms. **Corrected in Phase 1 (E27): those searches did not rank at all.** They returned the first ten stored items. The constant cost and count were the symptom, not a property of hybrid search.

### E22. Capacity mode follows traffic shape · Measured and Vendor

- **Serverless** suits intermittent traffic and costs nothing idle. Its ceiling cannot be raised, it carries an SLO rather than an SLA, and it runs in a single region.
- **Provisioned autoscale** suits sustained traffic that needs predictable performance.
- An account converts from serverless to provisioned one way only.
- Serverless measurements are floors, not guarantees.

Source: [Choose between provisioned throughput and serverless](https://learn.microsoft.com/en-us/azure/cosmos-db/throughput-serverless).

### E23. Keep no mutable copy of permissions · Design principle

The index stores no permissions, and the system of record is checked live. A stale index can then cause a miss, but never a disclosure.

This project applies the principle to agent–library attachments. The platform owns them, and the index holds only the immutable personal-library binding, which cannot go stale.

### E24. ~~There is no exact reference for a hybrid search~~ · Withdrawn (see E27)

*Withdrawn in Phase 1.* The observation below is real, but its explanation was wrong: the hybrid searches compared here were not ranked at all (E27), so the brute-force flag had nothing to change. Whether `VectorDistance(…, true, …)` inside a correctly routed `RRF` forces brute force is untested.

Forcing brute force with `VectorDistance(…, true, …)` inside `ORDER BY RANK RRF(…)` changed nothing observable. All 20 such searches cost exactly what their normal versions cost, retrieved the same documents and returned the same results. Outside `RRF` the same flag raised a vector search from 15 RU to 114 RU and read every chunk.

So recall "against exact search" is meaningful for vector-only searches only. The canary records hybrid agreement with the brute-force variant but does not assert it. Hybrid ranking quality has to come from an evaluation set with known answers (N3; `build-plan.md` Phase 5).

### E27. A hybrid query routed by partition key does not rank · Measured (Phase 1)

The Phase 1 exit run searched one 5,535-chunk library of 20 mixed documents through the API, with `partition_key=[libraryId]` as for vector search. All 11 searches, on unrelated questions, returned the same ten chunks (the first ones written) at exactly 3.91 RU in one HTTP response: the Phase 0 pattern (E21).

A diagnostic ran four of the questions five ways against the same library:

| Query | Partition key | Ranks? | RU | HTTP responses |
|---|---|---|---|---|
| `ORDER BY RANK RRF(VectorDistance, FullTextScore)` | `[libraryId]` | No: same ten items for every question | 3.91 | 1 |
| the same | none (library in `WHERE` only) | Yes: the right document first for all four | 128–191 | 4 |
| `ORDER BY VectorDistance` | `[libraryId]` | Yes: the right document first for all four | 8–16 | 1 |
| `ORDER BY RANK FullTextScore` | `[libraryId]` | No | 3.91 | 1 |
| the same | none | Yes, but poorly (below) | 39–71 | 3 |

The cause, by inference from the response counts: given a partition key, the Python SDK (`azure-cosmos` 4.17) sends the query straight to the partition without fetching a query plan. `ORDER BY RANK` needs the SDK's client-side pipeline (query plan, global full-text statistics, component queries, fusion). Without it, Cosmos returns `TOP` items in storage order, with no error.

Consequences:

- Search defaults to vector-only (`SEARCH_MODE=vector`). Hybrid is a setting, and its queries are never routed by partition key (`src/fli/query.py` `partition_key_for`).
- Routed correctly, hybrid costs 10–15 times a vector search, in four round trips. Its full-text statistics are computed over the whole container rather than the library, so its cost may grow with the number of libraries, against E3's premise. Phase 4 measures it.
- Full-text ranking on its own put the same long chunks first for unrelated questions, because the lexical terms include common words ("who", "is", "how"). Hybrid still found the right documents through its vector side. A stop list belongs before any hybrid evaluation.
- The canary now fails when different questions get the same hybrid results (`hybrid_ranks`).
- Vector measurements (E8, E12, E25) are unaffected: vector search is correct when routed by partition key.
- The SDK floor is the measured version, `azure-cosmos` 4.17.1. Whether a partition key skips the query plan may differ between versions; the canary's `hybrid_ranks` check catches a change on live runs.


### E25. The vector index engages minutes after writes · Measured (Phase 0) and Vendor

Microsoft documents that vector indexing is asynchronous. On a new container, after loading 1,500 chunks in 56 seconds:

- every vector search scanned all 1,500 chunks at about 114 RU for 5½ minutes;
- the same search without the partition-key prefix started using the index at 339 seconds, and with the prefix one poll later, at 369 seconds;
- from then on, searches cost about 15 RU.

Two consequences:

- **Results stay correct while the index catches up.** Search just costs what a scan of that library costs. For a small library that is cheap; a 1,500-chunk scan is 114 RU.
- **Any measurement of search cost must first wait for the index.** The canary polls until a search stops scanning, and records how long it took. A first canary run that searched after 30 seconds failed for exactly this reason.

The partition-key prefix (`partition_key=[libraryId]` on a hierarchical key) made no difference to whether or when the index engaged. Only a new container was measured. How long a new library in an already-populated container takes is unmeasured (N12).

### E26. Search visibility follows `activeGeneration`, not `status` · Inference (design consistency)

Spec 0.3 had search return only documents whose `status` was `ready`, and set `status: processing` at the start of every reprocess. A replaced document therefore vanished from search while its new version was processed, contradicting the rule that the old generation stays searchable until the new one commits (`spec.md` §7.8). A failed replacement would have hidden the document until someone re-uploaded it.

The fix: visibility depends only on `activeGeneration` (and `deleting`). `activeGeneration` is `null` until the first generation commits, so half-written chunks of a first upload are never visible either. Not measured; it follows from the spec's own rules. The unit tests in `tests/test_ids_and_generation.py` encode it.

The purge follows the same rule. Spec 0.4 purged generations below the committed one, which missed chunks written by a generation that was abandoned or failed after the last commit. `purgeDocument` now deletes every generation that is neither active nor pending, which is safe because messages for one library are processed one at a time (§7.1). Since Phase 3 it also spares any generation above the newest one its read shows, which a stale read may miss (E28, E30).

### E28. Deletion must be retryable, and the worker must not trust its first read · Vendor and inference

Microsoft documents that under Session consistency a client that has not written to a physical partition reads it with Eventual consistency, from a single replica that may lag. The API and the worker hold separate Cosmos clients, so a worker reading a record the API has just marked `deleting` may get the earlier version.

Before the independent review (`review-response.md`), two paths left a deletion stuck:

- **A stale read in the worker.** `purgeLibrary` logged "ignored" and completed. `purgeDocument` took the generation-tidying path, which deletes nothing of the active generation.
- **A failed queue send in the API.** The record was already `deleting`, and the request failed with an unhandled error.

Either way the record stayed hidden as `deleting` for good, its chunks and blobs were never removed, and a second `DELETE` returned `404`, so nothing could retry it.

The fix (`spec.md` §5 rule 5, §7.1):

- A repeated `DELETE` queues the purge again, and a failed send returns `503 queue_unavailable`.
- A purge queued by a deletion carries `deleted: true`. If the worker does not yet see `deleting`, it raises, and the message is redelivered after a one-second pause.
- A purge that fails on its last delivery is logged; Phase 5 alerts on it.

Not observed live: it follows from the documented semantics, and `tests/test_worker_handlers.py` and the API tests encode it. Within one API process, a search reads with the same client that wrote `deleting`, so it sees the deletion. Across API instances (Phase 5) that is not proven (N13).

**Replacement (Phase 3)** meets the same semantics twice:

- **Ingest.** `PUT …/content` writes the new `generation` and `pendingGeneration` through the API's client, then queues `ingest`. A worker that reads the earlier version would see the generation as not pending and complete the message: the replacement would be lost, stuck as `queued`. The message names its generation, and generations only increase, so a record whose `generation` is lower than the message's is provably stale. The worker raises and the message is redelivered, as for `deleted: true`.
- **Purge.** The API stores a replacement's original before any worker read can show its generation. A purge that deleted "every original except active and pending" on a stale read would delete it. A lagging read can only under-report the newest generation, never over-report it, so the purge deletes only generations at or below the `generation` it reads, never above (E30).

A stale read that persists through all five deliveries dead-letters the ingest, and the replacement stays `queued` until the `PUT` is repeated. That is the same gap as a dead-lettered purge; Phase 5 alerts on both.

**Metadata (Phase 3).** `PATCH` writes a document's author, period and tags through the API's client, then queues `retag`. A worker that read the earlier record would copy the old metadata onto the chunks and complete, and filters would keep matching the old values until the next `PATCH`. Each `PATCH` raises the record's `metadataVersion`, and the message carries it. A record whose `metadataVersion` is lower than the message's is provably stale: the worker raises and the message is redelivered. One that is higher means a later `PATCH` has queued its own `retag`, which copies everything, so this one completes without work. Not observed live; `tests/test_worker_handlers.py` encodes it.

Source: [Consistency level choices](https://learn.microsoft.com/en-us/azure/cosmos-db/consistency-levels) (updated 2026-04-27).

### E29. Bound what one document can cost · Measured (locally) and inference

The upload limit bounds bytes, not work. How many chunks a file makes depends on how dense its text is: a 100 MB PDF that is mostly images makes a few hundred, a 100 MB CSV about 50,000.

**Measured** with the `cl100k_base` tokenizer, no Azure involved. Under `chunking-v1.0`, the heading was carried whole into every chunk and subtracted from the 500-token budget. An 1,800-token section made:

| Heading | v1.0 chunks | v1.1 chunks |
|---|---|---|
| none | 5 | 5 |
| 300 tokens | 14 | 6 |
| 450 tokens | 75 | 7 |
| 520 tokens | 1,650 | 7 |
| 5,000 tokens | (one per body token) | 17, none over 500 tokens |

At 520 tokens that is about 861,000 tokens embedded and about 238,000 RU of writes at E10's 144 RU per chunk, for 1,800 tokens of text. A DOCX paragraph styled as a heading is enough. `chunking-v1.1` carries at most 64 tokens of heading and puts the full heading at the start of the body. Headings of 64 tokens or fewer chunk exactly as before.

**Inference**, from the settings and E10:

- A 50,000-chunk document (about 20 million tokens) embeds for about 2¾ hours at the default 150,000 tokens per minute. The worker renews a session lock for at most an hour, so the message would be redelivered mid-flight and redone, up to five times.
- Its writes queue ahead of every other library's on the worker-wide write cap, and its embeddings share the deployment's quota with search. That breaks `spec.md` §7.1's promise that one library cannot starve the others.
- Its vectors take about 5 GB as Python floats in one worker.

Hence `INGEST_MAX_CHUNKS_PER_DOCUMENT`, default 5,000: about 2 million tokens, embedded in about 13 minutes. It is checked after chunking and before embedding, so refusing a document costs nothing. It is a setting, so raising it is a configuration change. The proper fix is to embed and write in bounded batches (`build-plan.md` Phase 5), which removes the starvation and memory limits, though not the lock limit.

### E30. Replacement: one original per generation, no generation reused, underfill reported · Inference (design consistency), with unit tests; underfill measured (Phase 3)

Three problems surfaced when document replacement was built (`review-response.md` findings 6 and 7, and one found in planning). Each follows from the rules already here, and `tests/test_documents_api.py`, `tests/test_worker_handlers.py` and `tests/test_search_api.py` encode them. The Phase 3 live check then observed the underfill (below). The other two were not observed live.

**An original per generation.** Originals were stored at `{libraryId}/{documentId}/original/{fileName}`, and blob versioning is off. A replacement with the same file name overwrote the original of the generation search was still returning. Had the replacement then failed, the active generation's original would have been gone, and with it the ability to rebuild that generation (E16). Each generation now has its own path, `…/original/{generation}/{fileName}`, chosen over blob versioning because it needs no infrastructure change and the record need not track version ids. Only the active generation's original is needed for a rebuild, so the purge deletes the others; blob soft delete keeps them for 7 days.

**A generation number is never reused.** Without a counter, the next generation would be `max(activeGeneration, pendingGeneration) + 1`. After a failed generation 2 that reads as 2 again. If the purge after that failure never ran, chunks such as `{documentId}:2:50…99` from the failed file survive, and when the new generation 2 commits with fewer chunks they become visible: content from a file that was never accepted. Deterministic chunk ids make this possible; a counter prevents it. The record's `generation` only increases. It is also what makes stale reads detectable (E28).

**Underfill is reported, not refetched.** The generation check (`spec.md` §8 step 6) drops chunks after Cosmos has chosen the top `2 × top`. Replacement makes this routine: the new generation's chunks are in the index, invisible, for the whole write, and they are near copies of the active ones, so they compete for the same slots. Then the old generation's chunks do the same until the purge runs. Two remedies were weighed:

- **Fetch again** at the maximum window (40) when the first window was full and fell short. About 10–16 RU more for a vector search and about 150 RU for hybrid, only when triggered, and no help when `top` is 20, since the window is already 40.
- **Report:** say how many passages were left out, and that searching again later may return more.

Search reports. It costs nothing, it is never wrong, and the shortfall lasts only while a document is written or awaits its purge. An agent told that results were withheld can search again, or treat a thin or empty result as provisional (§9.2). Whether a refetch earns its cost can be settled with Phase 5's evaluation set.

*Phase 3 live check* (`records/phase3-exit-lifecycle-20261023T210242Z.json`): a 30-page paper was replaced by a different 18-page paper under the same file name, and the replacement committed 9 seconds after the `PUT`. Of the 4 searches made while it was written, one, 6 seconds in, came back with **no results** and the note. It asked the question the new paper answers. The new generation's chunks, written but not yet visible, filled the whole 16-chunk fetch window (`top` 8). Four seconds earlier, before those chunks were written, the same question had returned 8 results from the old generation. After the commit, all 68 searches returned 8 results of the new generation. So underfill can reach empty, not only thin. It is total exactly when the new version matches the question better than the old one, which is when an agent most needs the note.

### E31. Proposals fill gaps, never overwrite, and never fail a document · Inference (design consistency), with unit tests

A proposal is a model's guess, and `spec.md` §9.1 keeps accepted values distinguishable from a person's. Auto-accept therefore only fills what is empty. Replacing a value someone set would silently undo a person's decision. Such a conflict stays a proposal for review instead.

The proposal call is an enhancement on the ingestion path. Failing the document on a chat outage or a malformed answer would make search depend on the chat deployment. So a failure is logged and the document proceeds without proposals.

Accepted proposals are merged before the chunks are written, so auto-accept costs no `retag` (E10: a patch may cost close to a write, §7.7).

`acceptProposals` is checked against the record the API reads. Under E28 that read may briefly miss proposals the worker has just written, and the request is then refused with `400`; repeating it succeeds.

`tests/test_proposals.py` and `tests/test_proposals_flow.py` encode these rules. Observed live in Phase 3 (E32): with auto-accept, 12 decks had their tags at `ready` and no `retag` ran; every author already read from a file was kept; a replacement's new proposals waited rather than replace accepted values. None of 89 proposal calls failed, so the failure path is still observed only in tests.

### E32. Retag costs about 80 RU per chunk; proposals are accurate but their vocabulary drifts · Measured (Phase 3)

From the Phase 3 exit checks on 2026-10-23 (`build-plan.md` Phase 3), on the serverless demo account.

**Retag cost.** Accepting proposals on 30 documents patched 1,415 chunks for 112,649 RU: **about 80 RU per chunk**, from 73 RU for a one-chunk document to 80 RU per chunk for a 239-chunk one. That is a little over half a write (144 RU, E10), so Cosmos does re-index most of the item, as §7.7 feared. A 239-chunk document patched in about 8 seconds. The filters check timed a tag change reaching a filtered search in 5.8 seconds. Retagging stays cheap for single edits and expensive for bulk ones: re-tagging a 100,000-chunk library would cost about 8 million RU, about $2 on serverless. Accepting at ingestion (auto-accept) costs nothing extra.

**Proposal accuracy.** Authors proposed for 36 text, Markdown and HTML files that carry no author metadata matched the sample corpus's answer key for 34. The other two were defensible: the writer of a transcribed 1917 letter rather than its transcriber, and an author without the editor. Periods were proposed for 12 of the 36, each one consistent with the content (a dated shift handover, a month of sightings, the poems' 1998–2022 span); none was invented for an undated document. With `gpt-5-mini`, a call took 4–16 seconds and used 370–3,200 prompt and 560–1,900 completion tokens. Office, PDF and email files carried their authors in metadata, so their author proposals were never needed.

**Vocabulary drift.** Told to reuse the library's keys and values, the model often did not. One library ended with `document type`, `doc_type`, `doc-type` and later `documentType`; `company=PDF Solutions` beside `company=PDF Solutions, Inc.`; and one-off trivia tags such as `cake=lemon and elderflower`. A tag filter matches exactly, so drift splits documents across spellings and fills the tool definition's tag list (50 most used) with near duplicates.

So a prompt is not enough, and parsing now maps each proposal onto the library's own spellings (`spec.md` §9.1, `Spellings` in `src/fli/proposals.py`): `documentType` becomes `document type`, `PDF Solutions, Inc.` becomes `PDF Solutions`, and authors such as `A; B` and `A, B` meet. It merges only spellings of the same words. Synonyms and abbreviations (`doc_type` against `document type`) still depend on the model reusing the vocabulary it is shown, and the prompt now asks for keys in snake_case and for shared facets rather than trivia. Spellings already stored are not rewritten; an admin can merge them with `PATCH`. Replayed over the live library's accepted vocabulary, the mapping merges 85 keys into 80, 141 tag pairs into 137 and 20 authors into 19. Most of that library's drift is therefore synonyms, abbreviations and one-off tags, which only the prompt addresses.

*Live, 2026-10-23.* The new prompt and mapping tagged 12 DOCX files, with auto-accept, in a library whose 12 decks had been tagged under the old prompt (26 keys, all already snake_case). None of the 12 calls failed. The DOCX files got 44 tags (3.7 per document, against 4.8 for the decks) on 13 keys, 7 of them already in the library. `document_type` was on all 12 and `topic` on 11. Every key was snake_case, and 8 keys were used by one document only, against 20 of 26 for the decks. No trivia like the earlier `cake=` tag appeared. Some tags were still too specific to filter on (`baked_good=croissant`, `technique=lamination`, a `hazards` value listing three hazards), and the pull toward reuse can overreach: a small-claims court guide was tagged `topic=grievance`, a value taken from the grievance procedure. One batch against another of a different file type is a direction, not a controlled comparison; the Phase 5 evaluation set should settle it.

### E33. An unparseable file is `encrypted` only when it shows protection · Measured (Phase 3)

The extractor inherited a rule that any file it could not parse, for a reason it did not recognise, counted as `encrypted`. That was meant to exclude rights-protected content safely. Live on 2026-10-23, a damaged `.docx` (a zip header followed by text) and a plain-text file named `.pdf` both failed as `encrypted`. An admin reading that looks for a password or a rights policy that does not exist.

Encrypted Office files arrive in an OLE container and protected PDFs carry an `/Encrypt` dictionary, so protection can be seen. The fallback is now `parser_error`, and `encrypted` needs that evidence: an OLE container under a modern Office extension, with no marker recognised, still counts as encrypted, since nothing else should arrive that way. `tests/test_extraction_and_chunking.py` encodes it. Either way the file fails and nothing of it is searchable, so the change affects only what the admin is told.

### E34. Whole-file tasks need a complete retrieval path alongside passage search · Design, locally tested

On 2026-10-26 the user described platform-developer feedback: an answer about a complete document can need evidence beyond a RAG top-k window. The requested policy puts the scope decision with the agent, prioritizes answer quality, and accounts for input cost, latency and attention. The user also explicitly requires both directions within one question: reading full files then searching other files for supporting passages, or discovering passages then escalating to one or more full files, without routine confirmation.

The implementation adds document discovery, complete text reads and a transport-neutral tool bundle. It keeps full extracted sections before chunk overlap, checks active generations and content integrity, distinguishes legacy indexed text, exposes token estimates and all-or-nothing budgets, and keeps personal-library binding checks. Cosmos remains the searchable store; Blob additionally holds derived extraction artifacts handed to the model. This changes the earlier search-only tool/agent and storage descriptions in the specification. The documents definition gains the missing authored-date index; vector settings are unchanged.

`tests/test_reading.py` validates the deterministic service and scripted-agent paths, including both retrieval directions, mixed evidence, metadata pagination, lifecycle and refusal cases. It does **not** measure real-model scope selection, latency, Azure query behavior or answer-quality improvement. Those remain a live exit check in the build plan. Details: [document-retrieval.md](document-retrieval.md).

### E35. Adaptive retrieval smoke-tested live; quota and passage budgets affect the demo · Measured

On 2026-10-26, a dedicated test library ingested four real research PDFs (73 pages) with pre-chunking extraction artifacts. Catalogue paging, inspection, complete batch reads and budget refusal passed. A complete four-file response was estimated at 74,661 tokens, at 11.22 RU and about 0.90 seconds server time. The model then completed full-files → supporting passages and passages → complete file → supporting passages, with the intended complete-file selections verified. The passing metrics-only record is `records/document-retrieval-20261026T165258Z.json`.

The initial model run hit the prototype's token-rate allowance. A pre-fix run also withheld passage results near the remaining context limit, leading to extra model calls. The demo now fits a smaller, explicitly labelled set of passages into the remaining budget; complete-document reads remain all-or-nothing. Provider-directed retries and a bounded completion allowance handle transient rate limits. A smaller real paper made the multi-file model scenario fit the existing allocation. No quota increase was needed.

The final explicit-sequence smoke scenarios took about 81 and 68 seconds including rate waits, across six and four model calls. Input token usage is cumulative across those calls, not a single-file size. These results establish that the operations compose live; they do not establish uncoached routing accuracy or answer/citation quality. The record explicitly marks answer quality unassessed. Human testing and the broader evaluation remain required. Runtime environment identifiers are excluded from repository evidence.

### E36. Prototype performance views distinguish retrieval from model time · Measured

The library workbench's six-page smoke check captured 18 chunks, 6,584 actual embedding input tokens over two requests and 2,592.65 RU for chunk upserts (`records/library-performance-smoke.json`). The worker records these with the active generation; older documents remain unmeasured. Retrieval history is an optional external single-host journal with bounded scans, not production analytics or lifetime billing.

A manual mixed-file question followed catalogue → complete-file read → supporting passage search. Its client-side retrieval time was about 1.98 seconds and reported retrieval RU was 34.30; total response time was about 37.75 seconds. Four model requests occupied about 35.60 seconds, including about 25.74 seconds for the final answer, with no recorded rate-limit waits. Actual token usage was 50,128 input and 3,010 completion tokens cumulatively. These numbers separate the prototype model/orchestration behavior from the retrieval architecture; they are not scale benchmarks.

The answer's tool sequence worked but its source interpretation needed a correction: long-context positional bias does not establish that loading a whole original file cures missing evidence. The agent guidance now separates complete-coverage/global-synthesis needs from attention costs, and proposed routing policies from cited findings or specialized trained critics. Retrieval correctness, performance and presentation remain the priority; answer-quality evaluation is a separate gate. Details and exclusions: [observability.md](observability.md).

### E37. Mixed evidence reduced final context, but not cumulative cost in a manual pair · Measured

Two fresh chats with the same library and agent instructions compared a broad request (two complete files) with explicit per-source scope (one complete primary file plus eight reference passages). The mixed sequence worked without user consultation between operations. Final-answer input fell from 17,670 to 11,078 tokens, a 37.3% reduction. Cumulative model input increased from 25,161 to 26,223 tokens; reported retrieval RU increased from 17.61 to 29.01 and retrieval time from 0.96 to 2.05 seconds. End-to-end time was about 52 seconds for both. No rate-limit waits were recorded.

This demonstrates selective context and live tool composition, not a general RAG cost/latency advantage or measured improvement in model attention. Different wording, output lengths, cache behavior and an additional model call confound this single pair. Independent answer-quality assessment remains open. The consuming agent/builder owns scope judgment; the retrieval service owns clear affordances, provenance, completeness and costs. Metrics and limitations: `records/full-vs-mixed-retrieval-20261026.json` and [observability.md](observability.md#whole-files-versus-mixed-evidence).

### E38. Image meanings, visual return and vector-chart PDFs (2026-10-26) · Measured

The user requested that image meaning be searchable like passage text and that agents be able to find a vaguely remembered chart, read its explanation and reproduce it inline. They explicitly expanded Phase 3B to PDF vector charts. Implementation and consuming-platform contract are in `image-retrieval.md`.

`records/phase3b-images-20261026.json` is a live smoke check using four invented fixtures: a GDP chart drawn with PDF path commands, regional-margin pictures in DOCX/PPTX, and a standalone rainfall chart. All four ingested with an image chunk and measured vision usage. A vague GDP request retrieved the PDF visual page. Read-only, HTTPS, blob-scoped user-delegation SAS URLs were fetched successfully. A complete-document read returned the image and its source explanation. A regional-margin search found the deck. Actual model runs for both questions supplied native image content and returned inline image references with source discussion; the Streamlit UI displayed the GDP chart inline. An isolated live personal-library check also passed image replacement/deletion and binding/cross-library refusals. All 510 local tests, lint, formatting and the private reference-privacy check passed. Runtime IDs, source descriptions, answers and signed URLs remain outside the repository.

The check demonstrates the mechanism on this small corpus. It does not establish broad semantic recall, numerical transcription accuracy, general answer quality, DiskANN performance at scale, or whole-corpus visual completeness. PDF page rendering deliberately preserves vector drawings and context rather than claiming precise figure crops. Decorative paths can select a page, size rules can skip compressed pictures, and the visual cap can omit later candidates. Existing files require reprocessing. Visual token accounting belongs to the consuming platform; serialized full-read estimates cover text/links only. Generation-specific storage and lifecycle checks preserve the existing publication/purge rules; already issued links remain capabilities until their short expiry.


### E39. Immutable publication and retry checkpoints for visual generations · Measured and inference

The Phase 3B architecture review reproduced five local failures: a late overlapping attempt replaced the published extraction/chunks; downstream failure repeated successful vision work; the 10 KB default dropped a readable 640×400 chart (4,392 bytes); PDF page borders consumed a visual cap before a chart; and an empty configured OCR result blocked a usable vector-only PDF. The historical reproductions are in `records/phase3b-architecture-review-20261026.json`.

New worker generations conditionally create one canonical plan in Blob before embedding. Every attempt consumes that plan. Chunk writes with `contentHash` use Cosmos create-only operations and verify the existing fingerprint on conflict, preserving later metadata patches. The text artifact is create-only and checked on conflict; image paths include normalized-pixel hashes. This closes the reproduced overwrite race without changing partition or vector policies. Legacy/import rows retain upsert behavior; incompatible older partial generations must be reprocessed with a new generation. Metadata retagging remains an explicit mutable operation.

Successful descriptions receive generation-local, write-once checkpoints keyed by pixels, context/location/name, prompt version and model configuration. A saved plan bypasses extraction, vision and proposals on redelivery. Vision transport uses shared worker concurrency/cooldown. Concurrent cache misses can still call vision twice, and embeddings can repeat after failure; this is not a global exactly-once billing guarantee. Checkpoints follow the existing generation purge/soft-delete policy. No image bytes or cached descriptions enter Cosmos beyond the existing searchable representation.

`records/phase3b-hardening-conditional-20261026.json` uses real Cosmos and Blob operations and real text embeddings with controlled vision replies for fault injection. An overlapping late attempt left the published artifact/chunks and retagged author unchanged, and a full read succeeded. A simulated embedding failure then resumed from the saved plan with zero new vision calls. Blob create-only semantics were also verified, and the isolated library was purged. `records/phase3b-hardening-smoke-20261026.json` repeats all four formats and both real-model chart scenarios with actual vision calls. All 523 tests, lint/formatting and the reference-privacy check pass.

The byte threshold is now disabled by default; the pixel threshold and cap remain. PDF selection excludes recognized rectangular page borders and isolated rules. Empty OCR text can fall through to visual evidence, while parser/protection errors and page bounds still refuse processing. Skipped-candidate counts appear in search coverage. These changes fix the controlled cases, not all visual classification: other decoration, repeated logos, native Office chart objects and dense-page resolution can still affect coverage. Source anchoring, selective visual previews and broad recall/scale evaluation remain open. Session consistency across API clients and aggregate capacity across worker processes retain their existing caveats.

## Not proven

These are the honest limits of the evidence. Phase 0 and Phase 4 of `build-plan.md` are designed to close the first ones.

- **N1. ~~This exact configuration has never been built.~~ Built and passing at small scale (Phase 0).** float16, spherical, `indexingSearchListSize` 300, a shard key and hybrid search deployed together, and their stored definitions match `cosmos/*.json`. The canary passed at 1,500 chunks in one library (E8, E12, E25). What remains open is behaviour at scale (N11).
- **N2. Library-only sharding is inferred.** The measurements cover type plus owner, not owner alone. Library alone means one query per search instead of one per document type. Small libraries are scanned either way (E12), so the open question is large shared libraries (10,000–100,000+ chunks). A comparison run on such libraries settles it.
- **N3. Hybrid ranking quality is unmeasured** (E21). Until Phase 1 it had never actually run ranked (E27).
- **N4. One physical partition only.** Everything was measured with the data on a single physical partition. A container spanning many partitions, at roughly 5 million chunks and up, is unmeasured. Each search should still reach one partition, since the library leads the key.
- **N5. Spherical is preview** (E6). Product quantization is the GA fallback, at somewhat lower recall.
- **N6. Microsoft asks to be contacted.** Microsoft's documentation tells users of vector search on containers with hierarchical partition keys to contact the Cosmos DB team so the account can be configured. Treat Microsoft's confirmation as a production prerequisite.
- **N7. Not workplace content.** Earlier measurements did not use workplace documents, spreadsheets, emails or image descriptions. E38 adds invented chart/image descriptions, but broad workplace-content validation remains open. The demo corpus (`build-plan.md`) should include all of them.
- **N8. ~~float16 write cost was not measured separately.~~ Measured in Phase 0:** about 144 RU per chunk, not the 91 measured on float32 builds (E10).
- **N9. Proposal quality is untested.** Nothing measures how good AI-proposed descriptions and tags are; the review-and-accept step exists for that reason.
- **N10. float16 is not in Microsoft's documented data types.** In October 2026, Microsoft's vector-indexing documentation lists `float32`, `int8` and `uint8`. A float16 container deployed, kept its definition and passed the canary, but its support status is unconfirmed. Ask Microsoft together with N6, before production. The fallback is float32 in a new container (E16).
- **N11. The DiskANN graph has not been observed in use.** At 1,500 vectors, Cosmos used the vector index in its `QuantizedFlat` mode (E12). The library size at which it walks the DiskANN graph, and that graph's cost and recall on this configuration, are unmeasured. `build-plan.md` Phase 4's large libraries should report the index type Cosmos used.
- **N12. Index lag for a new library in a populated container is unmeasured.** E25 measured a new container. A personal library created in a container that already holds a million chunks may engage faster or slower.
- **N13. Visibility across Cosmos clients is unproven.** The API and the worker hold separate Cosmos clients, and under Session consistency a client reads another's writes with Eventual consistency (E28). Within one API process, a search sees that process's own deletes. A delete handled by one API instance and a search on another, or a search after the API restarts, may briefly return the deleted document. A commit by the worker may be seen late, which is a miss, not a leak. Test it before running more than one API instance (`build-plan.md` Phase 5).
- **N14. ~~Proposal quality and cost are unmeasured.~~ Partly measured (E32).** Authors and periods were accurate on a 36-file sample, and the chat cost per document is recorded. The model did not reliably reuse the library's vocabulary. Whether descriptions steer agents well is still untested; the Phase 4 routing report and the Phase 5 evaluation set should show it.

- **N15. Adaptive retrieval routing is only smoke-tested live.** Local/scripted tests and two explicit-sequence live questions establish the tools and composition contracts, not that a real model always chooses the right files or full/passages mixture. Evaluate realistic whole-file, all-by-metadata, ambiguous and mixed-evidence questions in both directions, with actual token use, cost and latency. Include localized, whole-file, all-by-metadata, vague-reference and mixed routing tasks, plus vague, caption-only and topic-only figures. E53 now plans section/document summaries for discovery and previews; compare them against current discovery/context before changing ranking or embedding defaults, preserving topic-only image and ordinary text recall. Catalogue pages and multi-document reads are not transactional snapshots; N13 still applies.

- **N16. Filtered image recall at realistic scale is unmeasured.** Small figure smoke checks do not establish recall when image rows are only 2–5% of a large library or the index uses DiskANN. Phase 4 must compare image-filtered vector searches to exact references, reporting recall@k, RU, latency and observed index type. Keep default `filterPriority` until evidence justifies changing it.

### E40. PDF figures and source captions replace page images (2026-10-26) · Measured

Review of four larger user-supplied PDFs showed that PDF raster objects and vector paths are unsuitable figure classifiers. A scan is a page-sized raster; a text-only page can contain a vector header bar. The earlier visual-page path therefore spent the image cap on pages and decoration. The user asked for individual images/charts/maps, separate page text, and associated source captions.

PDF worker ingestion now uses Document Intelligence `prebuilt-layout` with `output=figures`. Body paragraphs and reconstructed table rows become ordinary text; figure crops come from the service's figure endpoint, including scanned illustrations and vector charts. Source captions remain in ordinary text and accompany the image as explicitly attributed context/metadata. No page rendering or primitive-based fallback is used. Existing ready `pdf_page` generations remain readable; reprocessing publishes a fresh generation atomically. Native DOCX/PPTX pictures also retain caption associations when their adjacent style/label and position support one.

Selected-page live probes covered all four source roles: 18 pages yielded 12 figures. The report probe's text-only pages did not become images; a page in the scanned-book probe yielded two separate illustrations. Three detected captions were retained in ordinary text and associated with their figures. Crops were visually inspected on the raster and vector samples. Runtime source bytes, captions, crop images and service operations remain outside the repository. The user's explicitly requested documentation header is included separately as a supplied illustration.

`records/phase3b-figures-20261026.json` records the probes and a fresh four-format live smoke check. In that check all four files ingested; vague semantic retrieval, cropped PDF return, read-only image fetches, complete-document text/images and both real-model inline answers passed. All 537 local tests passed, including exact page-coverage refusal, figure-cap/text separation, interrupted-batch reuse, source-caption preservation, service authentication boundaries and shared layout throttling. No Cosmos container, index or partition change was needed.

Layout requests select at most 20 pages by default and freeze completed batch text, caption metadata and copied figure bytes under the generation prefix, so later retries do not depend on the service's temporary results. A shared governor permits two active layout operations per worker process. The default layout page limit is 2,000, checked before calls. Layout is billed per analyzed page, separately from the image-description cap; current selected-page requests resubmit the original source bytes and can incur upload overhead on large files. A failed unfinished batch can repeat; successful batches and vision descriptions are reused.

These checks demonstrate the revised mechanism and selected real-document segmentation. They do not establish broad figure/caption accuracy, numeric transcription accuracy, semantic recall or large-scale Cosmos performance. Learned layout can still miss, merge or misassociate visuals; size rules and the 50-image cap can omit candidates. Full-source library replacement runs are tracked outside the repository and will be recorded separately when complete. The platform's visual-token budget, session-consistency caveats and per-process capacity limits still apply.


### E41. Publish complete text before deferred image enrichment (2026-10-26) · Measured

The user requested that expensive image descriptions stop blocking already usable text. Ingestion now freezes canonical preparation, publishes all extracted text and text embeddings as an immutable snapshot, and queues `enrichImages` behind uploads already queued for the same library. It keeps the existing containers, partitioning, vector settings and durable session queue. Image-only files have no earlier text stage.

The document's generation identifies the source; `contentRevision` distinguishes text-only and enriched snapshots. `publishedChunkCount` hides unfinished image chunk writes. Enrichment appends image chunks without rewriting/re-embedding published text and atomically switches the extraction descriptor to a different immutable Blob. Complete-text reads disclose `visualsComplete: false` while images are pending/failed and recheck content revision to avoid mixed snapshots. Image failure preserves searchable text; replacement/deletion/supersession and personal-library checks remain in force. Unpublished same-generation artifacts remain inaccessible until generation cleanup.

All 545 local tests passed. New regressions cover early text search/full reads, queued uploads publishing text before enrichment, image failure preserving text, enqueue failure/retry without repeated text embeddings, unpublished image visibility, enrichment during a complete read, replacement/deletion between phases and metadata edits/duplicate delivery. Live checks against two larger uploads confirmed text search and complete-text reads while 48 and 50 images respectively remained pending; the API disclosed the incomplete visual coverage. `records/text-first-ingestion-20261026.json` contains role labels, counts and usage only. A fresh four-format live run also completed deferred image publication, vague chart search, signed-image fetches and complete reads with images (`records/text-first-enriched-smoke-20261026.json`).

This is staged publication, not per-page streaming. PDF layout and crop preparation, text embeddings and metadata proposals still precede initial text publication. Images become visible together when enrichment finishes. Session order means a newly uploaded file can still wait behind an image job that has already started; separating worker workloads for strict text priority would be a further scheduling change. Per-process capacity and existing cross-client Session consistency limitations still apply.


### E42. Select answer images, recover captions, and read known PDF pages beyond the cap (2026-10-26) · Measured

Two user questions exposed distinct issues. Search returned its default eight ranked candidates, while the demo rendered all unselected image hits as a gallery. One correct diagram also carried an inherited section heading, which the answering model called its label. In the illustrated source, indexed images stopped at page 44 under the 50-image cap; the requested picture was on page 94. Its text was searchable, but its picture was absent from semantic retrieval. Whole-file reads then hit the configured context budget despite the question being localized to one page.

The answering adapter now distinguishes section context from source captions, omits inherited context absent from nearby page text, and uses filename/page citations instead of opaque extraction figure numbers. The demo displays only images explicitly selected in the answer. Other image candidates are diagnostic text rather than a gallery of implied matches. Native search previews are limited to the leading image candidate, preserving input capacity for targeted source inspection. Explicit render handles accompany visual previews; an initial model replay that still omitted the image was caught before completion.

`POST …/documents:images` / `get_images_library_<name>` reads 1–3 identified PDF pages, returning selected-page text and at most 10 cropped source figures. This bypasses the semantic-description cap for source reading while retaining that cap for indexing cost. It makes no extra vision/embedding calls or Cosmos chunk writes. Generation-scoped copied crops/page text are checkpointed; matching indexed crops retain their existing image IDs via the immutable plan. Personal binding, isolation and lifecycle rechecks apply, including cleanup after a purge races materialization.

Source captions are returned and displayed separately. When layout misses an association, a conservative geometric fallback considers only aligned, close text with an explicit figure label or source attribution, excluding headings, ordinary prose, other pages/columns and ambiguous ties. It marks the attachment `adjacent_inferred` and preserves the source text. The selected diagram's actual adjacent caption was recovered this way. Missing caption metadata is looked up only for already-selected images, at most three source pages per turn. Recovered captions can trigger one context-bounded answer revision so the answer and displayed metadata agree; that revision cannot introduce new image selections. Retrieval and model usage include the extra work.

All 573 local tests passed. The two original questions were replayed with the actual answering model: one selected the correct diagram, and one retrieved the requested later-page picture using the new tool; both returned exactly one selected image inline. The diagram's associated caption accompanied that selection. Live source crops and cache-stable IDs were verified, with source bytes, prompts, answers and signed links held outside the reference repository. Evidence: `records/visual-answer-selection-20261026.json`. This is a small real-question regression check, not broad caption accuracy or semantic recall proof. Existing ingested descriptions remain immutable; targeted page lookup supplies missing source figures/captions without a full re-ingest.


### E43. Pair and store captions during ingestion; retrieval is read-only (2026-10-27) · Measured

The user clarified that an image's caption belongs with that image at ingestion. The prototype's post-draft caption recovery and extra answer-generation pass were the wrong normal path. They are removed. Caption extraction/association now completes before image publication, and indexed image metadata and searchable image text carry the same source caption or an explicit `not_identified` status. Captions remain in ordinary text too. No caption-only lookup or caption-driven answer rewrite occurs during an answer.

An immutable generation-local source manifest stores qualifying image bytes/references and caption associations beyond the semantic-description cap. The AI-description limit remains 50; source storage has a separate configurable cap of 1,000, with size/format/count omissions recorded by PDF page. PDF preparation retains metadata-only handles and native picture preparation uploads one image at a time, avoiding a memory-sized list of PNGs. The manifest publishes with the text snapshot before slower semantic descriptions finish. Selected-page reads validate and load stored manifests/page text and mint image links; they do no original-PDF parsing, layout calls, model calls or storage writes. Legacy files require reprocessing instead of answer-time repair. No Cosmos container, index or vector-policy change was required.

The agent receives image/caption metadata in the same tool result and displays the caption by default. The demo supports an explicit caption-omit rendering option while retaining source provenance. Existing active generations were rebuilt from the exact original bytes, reusing matching description checkpoints; old generations remained readable until publication. Stored-source and indexed-image coverage are reported separately, and complete text reads disclose additional source images outside their indexed image sections.

All 577 local tests passed, including source pairs available before vision enrichment, semantic/source caps, manifest integrity, missing originals at retrieval, lifecycle/isolation, default/omitted caption display, and model answers without recovery or revision calls. The two live documents stored 138 and 48 source images, with 50 and 48 semantic image chunks respectively. The real-model diagram answer needed only search and returned its caption; the later-page illustration answer used search plus a stored-page read. Each selected one image, with no caption-revision call. Live validation and counts are recorded in `records/ingested-image-captions-20261027.json`; runtime identifiers, captions, source bytes and answers stay outside the repository. These are mechanism/regression checks, not a broad caption-detection or retrieval-recall evaluation. The earlier E42 timings and recovery workflow are historical and superseded by this path.


### E44. Final image-path efficiency and readiness review (2026-10-27) · Verified locally and smoke-tested live

A final source review retained the current Cosmos/Blob division, shared text/image vector space, library-scoped queries, immutable publication and stored-caption retrieval. It found that enrichment eagerly downloaded all retained images and attempted to create their already-persisted blobs again. Enrichment now loads one crop at a time, releases its bytes after description and reuses its reference. A regression test verifies ordered download/description and rejects duplicate crop uploads. PDF normalization and text-section assembly run off the async event loop. The unused PDFium dependency from whole-page rendering is removed. A real-model replay also selected a same-subject image from a different page than the matching text. Native previews now supply an image only when it leads the overall result ranking, avoiding promotion of a lower-ranked picture over a leading text hit. All candidate descriptions/links remain available; an added scripted check covers both ranking orders. The later-page replay then selected the correct source page. This is a regression observation, not proof of deterministic model routing.

Coverage now keeps legacy visual omissions from claiming `visualsComplete`, and distinguishes extra PDF figures available by page from unindexed Office pictures that current direct-read tools cannot expose. Agent/tool guidance explicitly supports relevant figures alongside ordinary explanations, reuses returned image/caption evidence and avoids unnecessary caption repetition in the demo. The README, specification, integration guide and build plan describe the final path, rollout and caps together.

All 581 local tests passed on Python 3.14. Lint, formatting, packaging, documentation links and the private reference-privacy check are part of the readiness record. Real-model smoke checks are reported separately from scripted tests; neither establishes broad caption/figure recall, ideal illustration choices or production-scale performance. The earlier Session-consistency and aggregate-capacity caveats remain. Review: `docs/consults/2026-10-27-image-readiness-review.md`. Evidence: `records/image-readiness-review-20261027.json`.

### E45. Compact evidence, interleaved reads and bounded retrieval/vision follow-ups (2026-10-27) · Verified locally and smoke-tested live

Issue #2 was reviewed against the original priorities: answer accuracy before response speed and cost, autonomous mixing of full files and passages, ingestion-time captions and early text publication. The demo now projects tool text separately from visual attachments, removing signed links, geometry and telemetry while retaining full-read section text, source provenance, captions, limitations and size hints. Native image inputs and selected-image rendering still use platform-held attachments. Raw REST responses and service budgets remain compatible; API estimates conservatively count their larger JSON. Per-document tokenization runs off the event loop. Context accounting uses the last provider-reported prompt usage plus estimated additions; new/unreported images retain a configurable 8,192-token fallback and the answer reserve.

New enriched snapshots interleave figures by PDF page, PPTX slide or DOCX paragraph. Text-only snapshots and published text chunk IDs/embeddings remain unchanged; existing frozen generations and legacy indexed reads keep their order. Search exposes parent-file token size/representation using document records already read for visibility checks. Optional `distinctDocuments: true` selects the best visible passage per file within 40 ranked candidates, with per-file window counts and explicit non-exhaustiveness. Ordinary passage search, library binding, generation/publication fencing and query routing remain intact.

Image enrichment now bounds loaded crops per document by `VISION_CONCURRENCY` and describes them concurrently under the existing worker-wide HTTP governor. Failure cancels siblings; output indexes remain in source order, successful checkpoints remain reusable, crops are not uploaded again, and partial image publication is still refused. This supersedes E44's strict serial loading while preserving a configured memory bound. It does not increase HTTP quota or establish aggregate worker capacity.

All 600 local tests passed on Python 3.14, including out-of-order completion, cancellation, bounds of one/two/three crops, intact text snapshots, projected tool evidence, provider-usage fallback/new-image accounting, document grouping and isolation. Ruff lint/format, diff checks and the private reference-privacy check passed. The four-format live corpus confirmed interleaving in PDF/DOCX/PPTX, standalone-image compatibility, grouped search with parent sizes and stored-page reads. Projected full-read JSON used 30.6–33.4% fewer text tokens on these small one-image fixtures; all section text was unchanged. These savings are response-text measurements, not total model-cost reductions.

Two real-model smoke questions returned valid selected image references with native image inputs and no signed links/geometry in tool text. The vague-figure question used one search, two model calls, 15.50 RU and about 14.2 seconds total (0.75 seconds retrieval). The whole-file/supporting-evidence question used discovery, search, a complete read and a stored-page read across four model calls, 38.95 RU and about 31.1 seconds (1.65 seconds retrieval). In the latter, the model requested the complete read and a redundant same-page image read together. This is an observed remaining orchestration inefficiency, not evidence that the storage design needs both operations. These runs demonstrate composition/presentation, not broad answer quality, recall, fewer rounds or a controlled latency improvement. No before/after ingestion benchmark establishes a speedup from concurrency.

Evidence: [metrics-only live record](../records/retrieval-followups-20261027.json). Decisions, deferrals and rollout: [review record](consults/2026-10-27-retrieval-followups.md). Summary-vector rows and shorter image embedding context remain gated on labelled recall evidence; the REST `view=agent` envelope remains a platform-contract decision. Phases 4/5 now name these gates, realistic image-filter recall and the expanded routing/figure evaluation set (N15/N16). No Cosmos container, partition, vector policy or embedding model changed.

### E46. Natural multi-turn testing exposed a document-grouping trap and evidence-selection gaps (2026-10-27) · Observed live; read-only diagnostics

A natural request for meeting-ready research evidence and useful figures exposed a practical integration gap in the new optional document-grouping mode. The agent issued eleven searches, each returning one best passage per document, while trying to collect evidence inside one already identified long report. A full-read attempt was refused by the configured context budget. The demo exhausted twelve tool rounds and made a forced final answer: thirteen model calls, 321.88 retrieval RU, 101,715 model input tokens and 105.96 seconds, including a 30-second provider quota wait and 9.82 seconds of retrieval. Repeated grouped searches are not evidence that only one relevant passage existed.

The grouping implementation followed its contract, but the tool presentation and consuming loop did not recover from an inappropriate mode. The compact view retains the grouping note and per-file window count but omits the explicit coverage mode flag, and the usual count-based `thin` disposition is ambiguous for an intentionally one-file selection. Treat this as a usability/integration issue to fix, not as a general Cosmos recall or storage failure. A read-only replay returned one result grouped and eight ungrouped; the verbose ungrouped query still favoured introductory material, so ungrouping alone is not a proven answer-quality fix. Focused topic searches retrieved later relevant material; a targeted memory/context passage was found in the index.

A separate service-level read verified stored text and image sections for relevant later pages. Its projected response was about 97,683 tokens (43,868 body-section tokens, 39,639 image-description tokens, plus metadata), beyond the demo's 48,000-token context budget before history/output reserves. Full-read refusal preserved the intended completeness contract. Document-ID-scoped passage retrieval and bounded section/page reading merit explicit API/platform review as a middle path between ranked snippets and all-or-nothing full files; existing selected-PDF-page image reads already return page text but are described for visual lookup. No container/vector-policy change is justified by this observation.

Two image follow-ups each retrieved eight candidates. The first mostly listed them and offered another step; only after a further user request did four selected figures render. This is a model-routing/presentation defect despite explicit existing guidance to show useful returned images. The selected figures existed, but several were only broadly related to the requested context/continuity thesis. The summary overstated what its retrieved citations established and did not supply verified quantitative support. Directly relevant context/memory material was present elsewhere in the report; it supports narrower claims rather than a universal quantified benefit from adding context. The existing demo also retains prior user/assistant text but drops tool evidence/native inputs across turns, requiring fresh evidence delivery on follow-up.

Across the three original turns: 349.53 retrieval RU, 133,023 model input tokens, 9,070 output tokens, 495 query-embedding tokens, seventeen model calls, 142.53 seconds summed response time and 11.86 seconds retrieval. Diagnostic requests were recorded separately. The model, source generations, app settings and index were not changed during review. The next integration work should make retrieval mode/progress clear, improve focused query formation and stagnation recovery, preserve caption/relevance distinctions, and act on requested visuals without routine permission loops. Broader ranking/recall evaluation remains necessary; these probes do not establish an ANN recall score or a generally superior library organization.

Metrics and content-free findings: [casual test record](../records/casual-retrieval-friction-20261027.json). Visible conversations, source content, images, query text and runtime identifiers remain outside the repository. This extends E45's validation: deterministic contracts passed, but natural tool use revealed an unresolved integration gap in the newly added grouping option.

### E47. Separate versus combined libraries compared in a human-run eight-question pair (2026-10-27) · Observed live

A human ran the same eight prompts against an agent with four single-document libraries and an agent with one library holding the same four files. Each suite used one consecutive conversation, the same agent instructions/model/context/image settings and identical original bytes; image descriptions were independently generated. Both suites rendered all seven requested image placements. Principal fixture facts matched, with caption-provenance and unnecessary full supporting-file reads still observed. Raw answers/images were captured privately before switching agents.

The separate run used 310,458 model input tokens, 13,505 output tokens, 149 query-embedding tokens and 135.78 retrieval RU; the combined run used 129,764 input, 12,928 output, 148 query-embedding tokens and 198.82 retrieval RU. Summed response time was 226.22 versus 150.31 seconds, including 60 seconds of quota waits in the separate run and none in the combined run. Retrieval itself took 8.99 versus 8.38 seconds. There were 26 versus 21 model calls and 18 versus 13 tool calls. The separate agent exposed sixteen library-bound tools, versus four; first-request input before retrieval was 8,701 versus 3,562 tokens.

These observations apply to this adapter and model behaviour. Fixed run order, caching/quota, different schemas/vocabulary, independently generated descriptions and retained conversation text prevent a pure storage-layout causal conclusion. The combined variant used fewer model tokens but more RU. No dollar-cost, broad recall or universal library-organization claim follows. The associated eight-copy ingestion snapshot records vision/embedding tokens and write RU separately; long elapsed completion for combined-library Office files includes deferred-session waiting, not merely vision execution.

Evidence: [paired metrics/ingestion record](../records/library-layout-manual-20261027.json); [test review and limits](consults/2026-10-27-agent-retrieval-test-review.md). This pair compares library configurations, not the implementation before and after issue #2.

### E48. Casual image references found the right source but exposed ambiguity and modality gaps (2026-10-27) · Observed live

Two casual references preceded E46's three-turn meeting-brief case. An informal diagram comparison found and displayed the correct figure using one search and two model calls (21.50 RU, 9,881 input/1,225 output model tokens, 26 query-embedding tokens, 14.64 seconds total/0.88 seconds retrieval). The comparison was correct under a plausible interpretation, but the intended referent remained ambiguous, the answer's opening could read like the opposite conclusion, and caption uncertainty was stronger in the renderer than in prose.

An ambiguous plot/water-marker question found the correct map and reported its markers/waiting-list facts correctly. It did not explicitly address a plausible plot-number interpretation. Two initial searches restricted results to text; discovery plus a complete guide read subsequently recovered the image. That path used four tools/five model calls (29.37 RU, 28,488 input/2,139 output model tokens, 50 query-embedding tokens, 29.31 seconds total/1.83 seconds retrieval). No provider quota wait occurred. The earlier diagram exchange remained in conversation history.

The source facts and image selection were checked against the original figure/map, not just generated descriptions. These are focused reviewer findings; they do not confirm the user's intended referent or establish broad semantic recall. They motivate explicit ambiguity handling, mixed/visual evidence access and clear separation of source captions, image text and interpretation. Evidence: [casual cases record](../records/casual-image-reference-20261027.json). Together with E46, these support the [proposed strategy](consults/2026-10-27-agent-retrieval-strategy.md), which is not implemented by this documentation change.

## E49: Agent capability corrections after natural tests (2026-10-27)

**Implemented after explicit user authorization.** [The contract](agent-retrieval.md) separates bounded semantic file selection from passage evidence, removes grouping from the agent search schema while retaining REST compatibility, adds document/generation/revision-scoped search and snapshot-bound evidence handles, provides all-or-nothing selected source reads plus paged outlines, and permits direct stored image-ID inspection across supported formats. Compatible existing artifacts require no re-ingestion; legacy precision/manifests are disclosed. The 50-image semantic cap was not the principal failure: relevant evidence was already available. No cap, vector/container policy, embedding input or quota was changed.

The adapter retains reference hints for revalidated follow-ups, signals repeated evidence extents, distinguishes UI attachment availability from actual native-input delivery, preserves caption/source provenance, and uses at most two repair calls for mechanically detectable answer omissions within the unchanged round/context bounds. It never displays every candidate automatically. These are capability/contract changes; prompts and deterministic checks cannot establish general relevance or resolve every ambiguous intent.

Local validation covers mode separation, SQL/routing invariants, scoped empty-result revision races, full/selected batch budgets, outline paging, stale/foreign handles, legacy overlap/precision, personal isolation, retained unindexed Office images without writes, repeated extents, reference-only session state, selected native inputs and bounded answer repair. Live read-only probes confirmed scoped queries and source expansion against existing Cosmos/Blob artifacts. Four iterative real-model replays used unchanged source snapshots and the same deployed model/48k budget. [Captures and assessment](testing/2026-10-27/agent-fixes/README.md); [metrics](../records/agent-capabilities-20261027.json).

Later replays delivered plural report images in the first meeting answer and recovered numerical source findings, avoiding the original eleven-grouped-search trap. The latest final follow-up stayed on the report after a reminder fix. The map's possible Plot 4 referent, inferred-caption qualification, causal overstatement and unsupported reuse/service claims remain model-quality findings. A shorter/cheaper answer is not accepted merely because tool checks pass. Single iterative paths do not prove general latency, RU/token savings or recall; repeated held-out evaluations remain required. No production source/answer logging was added.

Validation for this implementation: **611 tests passed**, Ruff lint/format and private reference-privacy checks passed. Original replay captures remain immutable; the new sanitized pack includes all four iterations and live probes, including remaining quality failures.

## E50: Keep the library and agent-development scopes separate (2026-10-27)

After E49, an uncommitted attempt to address ambiguous wording and research overstatement expanded into extra interpretation/semantic-review model calls and numeric-homophone handling. The user clarified that the ambiguous reference was a typo and explicitly challenged the scope expansion into agent development. The experiment was removed; [the boundary decision](consults/2026-10-27-library-agent-scope.md) and [sanitized diagnostic runs](../records/agent-scope-experiment-20261027.json) preserve why. Schema/quotation failures and degraded fallback answers are not accepted as quality gains.

The retained change is ordinary reference-adapter guidance to state what a cited study's intervention/comparator actually establish and keep broader context/memory hypotheses qualified. It adds no extra model call or sharing gate. Retrieval/provenance remain the library's responsibility; subject relevance, interpretation and scientific claim strength remain the consuming agent's. Existing E49 capabilities remain intact. Do not resurrect the removed experiment as implicitly approved work.

## E51: Browser rerun left the old demo adapter in memory (2026-10-27)

The first new-agent browser test failed with `TypeError` before retrieval, despite the API serving the current four-tool bundle. A temporary content-free diagnostic of the **live UI process** showed that its imported `run_turn` still lacked `evidence_ledger`, while the updated page supplied that keyword. Streamlit **Rerun** had refreshed the page without refreshing that imported function/cached process state. Fresh-process API/model replays had not exercised this deployment mismatch. The earlier claim that a browser rerun alone made Chat ready was incorrect.

Restarting only the Streamlit process reloads the adapter and cached clients; persistent agents, attachments, files and indexed generations are preserved. The temporary diagnostic was removed. This is a process-deployment correction, not a retrieval/storage or agent-policy change. Refresh API and UI processes together when their tool contracts change; do not infer live UI readiness from a separate Python test process.

After the UI process restart, the same user question completed in the browser with an answer and three source images: 5 retrieval calls, 67.21 RU, 65,363 cumulative input tokens and 3,158 output tokens; total response time 44.01 seconds. [Verification metrics](../records/ui-restart-verification-20261027.json) and [sanitized visible result](testing/2026-10-27/captures/ui-restart-success.txt). This proves the runtime mismatch is resolved, not broad answer accuracy or a performance gain.

## E52: Latest browser spots succeed on retrieval/display but retain integration inefficiencies (2026-10-27)

The refreshed Combined agent answered the casual Microsoft-report question and an explicit Plot 4 question. The report result found pages 26/27/36 and displayed three figures; the map result correctly answered that Plot 4 has no W marker and is not waiting-listed, identified waiting-list plots 5/8 and showed the guide map. Comparison used the committed source text and map. [Measurements and assessment](../records/latest-browser-spots-20261027.json).

The report answer still over-associates a figure: page 36's Promptions screenshot is explicitly an **ephemeral** UI example, while the answer uses it alongside a persistent-interface argument. Text on that page distinguishes ephemeral Promptions from persistent DynaVis/JELLY. Some across-session memory language is also an inference from narrower collaboration/interface evidence. These are consuming-agent source-association/interpretation findings, not proof of missing ingestion or index evidence.

Both answers used five retrieval calls and six model calls, without quota waits. Report: 44.01 s total, 3.39 s retrieval, 67.21 RU, 65,363 cumulative input / 3,158 output tokens. Map: 31.36 s total, 4.00 s retrieval, 61.01 RU, 51,382 input / 2,316 output. Four text-only searches preceded the map's small full read; the first three repeated the same three text locations, and a no-progress signal fired. Most wall time was model/network processing, with repeated prompt context contributing cost. Cached-input tokens are included in total input, not additional to it.

No native image pixels were supplied to the model in either turn. Returned images were displayed and their stored descriptions/context were available. These runs exercise ingestion-to-retrieval-to-display, including bounded source expansion, but not selected native visual inspection. A single pair of different questions is not a controlled recall/latency/cost benchmark and does not motivate expanding the library into a semantic sharing gate (E50).

## E53: User prioritized the next integration and retrieval roadmap (2026-10-27)

After reviewing the original issue #2 deferrals, the user requested [planned development](consults/2026-10-27-retrieval-roadmap.md) in this order: compact REST evidence/attachment responses for easy integration; section-aware image context with generated section summaries also useful for selecting/scoping text passages; and document summaries for both semantic file selection and human-readable interface previews. The user accepts extra ingestion model/embedding cost for meaningful section summaries and explicitly requires care not to lose information that helps vague image retrieval. A heading alone is not the desired section representation. These are planning decisions, not implementation or measured gains.

Microsoft's linked layout documentation supports section/subsection structure and figure-related elements, but current PDF code mainly chooses bounded same-page image context. The roadmap calls for preserving structural provenance, generating labeled section overviews from source content, and evaluating shared section-summary context and semantic section selection. It retains original evidence, uncertain-membership disclosures and broad-search fallback; summaries do not become a compulsory exclusion filter or substitute citations. Existing opening-text document descriptions remain discovery hints, not full-document summaries.

The prior requirement to demonstrate a file-selection failure before planning document-summary vectors is superseded by the additional interface-preview use case. Rollout still requires coverage/recall and cost comparisons, publication/lifecycle/backfill design and text-first availability. No runtime behavior, source data, embedding defaults, container policies or deployed processes changed in this documentation update. The library/agent scope boundary in E50 remains in force.
