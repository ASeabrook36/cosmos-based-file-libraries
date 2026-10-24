# Serverless for the demo, provisioned throughput for production

*Version 1.0, 2026-10-24. The full argument behind `spec.md` §11's one-line recommendation, and guidance for sizing and switching. It draws on Microsoft's documentation (checked 2026-10-24), this project's measurements (`evidence.md`) and arithmetic on them. Prices are Microsoft's published examples; check the [pricing page](https://azure.microsoft.com/pricing/details/cosmos-db/) for current regional rates.*

## Recommendation

- **Demos, pilots and small organizations: serverless.** It costs nothing when idle, needs no capacity planning, and runs exactly the same data design. The demo runs on it, and every measurement in this repository was made on it.
- **Production for any organization with sustained daily use: provisioned autoscale, with dynamic autoscale turned on.** Serverless has a fixed ceiling, shared by every library in the organization, that cannot be raised. Provisioned throughput can be raised, divided per partition and protected from bulk uploads.
- **Starting on serverless does not lock in anything about the design.** The partition keys, vector settings and container definitions are identical in both modes. An account converts from serverless to provisioned in place, one way (E22), without moving data.

## How the two modes work · Vendor

| | Serverless | Provisioned (autoscale) |
|---|---|---|
| Billing | Per request unit consumed, about $0.25 per million RU | Per hour, for the highest RU/s used in that hour, never less than 10% of the configured maximum. Autoscale is billed at 1.5× the manual rate, about $0.012 per 100 RU/s per hour in Microsoft's example |
| Throughput | At most 5,000 RU/s per physical partition; "no assurances of predictable throughput or latency" ([Serverless performance](https://learn.microsoft.com/en-us/azure/cosmos-db/serverless-performance)) | Up to the maximum you set: 10,000 RU/s per physical partition, 1,000,000 RU/s per container by default, more by support ticket ([Service limits](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)) |
| Raising the ceiling | Not possible. Capacity grows only when partitions split, at 50 GB of data each | Raise the maximum at any time; immediate up to 10,000 RU/s per existing partition, otherwise a split of 4–6 hours |
| Per-partition control | None | Dynamic autoscale scales and bills each partition on its own; redistribution (preview) moves throughput onto a hot partition |
| Guarantees | Latency target (SLO) only: under 10 ms for point reads, 30 ms for writes | SLA |
| Regions | One | Any number |
| Idle cost | Zero | 10% of the maximum, per container |

Sources: [Provisioned throughput and serverless compared](https://learn.microsoft.com/en-us/azure/cosmos-db/throughput-serverless), [Autoscale FAQ](https://learn.microsoft.com/en-us/azure/cosmos-db/autoscale-faq).

## What this project measured on serverless · Measured

- **The ceiling in practice:** one serverless container topped out near **1,785 RU/s**, about 36% of the documented figure, and reached it with about two concurrent writers (E10).
- **Writes:** about **144 RU per chunk** at float16 with the full-text index (E10, Phase 0). Two writers sustained 26.7 chunks per second; eight exhausted the SDK's retries within about 100 writes.
- **Searches:** about 15 RU per vector search on a 1,500-chunk library (E8, E12); 8–16 RU on a 5,535-chunk library (Phase 1); 29.87 RU for a demo search with neighbours (Phase 2). Filtered searches cost 4–15 RU (Phase 3).
- **Hybrid searches** cost 128–191 RU when correctly routed (E27). They are not the default.

Request charges per operation do not depend on the capacity mode, so these figures carry over to provisioned throughput. **Ceilings and latency under load do not carry over:** they describe serverless.

## Why serverless does not suit a large organization

### The whole organization shares one ceiling

A serverless container stays on one physical partition until it holds 50 GB, about 5 million chunks (E11). Until then, every library in the organization shares that partition's capacity: measured near 1,785 RU/s, documented at most 5,000.

At 15–40 RU per search on `chunks`, that is roughly **45–330 searches per second for the entire organization**, before any uploads. An organization of a few thousand people with modest use fits comfortably. A large organization at peak may not, and nothing can be bought to raise it.

### Uploads take capacity from searches

Writes are the expensive side. Two writers at 26.7 chunks per second use about 3,800 RU/s, more than the measured serverless ceiling. So during a large upload, searches across every library slow down or are throttled. `spec.md` §7.1's session lanes stop one library's upload from blocking other libraries' *uploads*. They cannot create capacity for *searches* when there is none. On provisioned throughput, the ingestion budget below reserves room for searches.

### No levers

When serverless runs out, the only remedies are waiting or uploading more slowly. There is:

- no maximum to raise;
- no per-partition scaling;
- no redistribution;
- no forced split;
- no second region.

The levers in `untested-at-scale.md` for a busy library (dynamic autoscale, splitting, redistribution, read regions) all require provisioned throughput.

### No SLA, one region

Serverless offers a latency target, not an SLA, and runs in a single region. A production service that agents depend on during the working day should have the SLA, and the option of a second region for availability.

## Cost: when each mode is cheaper · Inference from published prices

Per RU actually used, serverless costs about 7.5 times autoscale at full use:

- **Serverless:** $0.25 per million RU, which for a load of *A* RU/s is about *A* × $0.0009 per hour.
- **Autoscale:** about *P* × $0.00012 per hour, where *P* is the highest RU/s used in that hour.

So **serverless is cheaper when average use is below about one eighth of the hourly peak**, which describes intermittent traffic. Once use is steady through the working day, autoscale is cheaper, and it also has headroom.

Two illustrations, at Microsoft's example prices, for one organization. Each search is taken as about 30 RU across all three containers.

**A pilot.** 50,000 searches a month and one upload of 100,000 chunks.

- *Serverless:* about 1.5 million RU of searches ($0.38) plus 14.4 million RU of writes ($3.60). **About $4.**
- *Provisioned:* three containers at the smallest autoscale maximum of 1,000 RU/s each bill at least 100 RU/s each, every hour. **About $26 a month before any use.**
- *Serverless is the right choice.*

**A large organization in steady use.** 50 searches a second through 10 working hours a day, 22 days a month, plus regular uploads.

- *Serverless:* 50 × 30 RU is 1,500 RU/s, about 1.2 billion RU a month, **about $300**. And at 1,500 RU/s it would be running at roughly 85% of the measured serverless ceiling, with nothing left for uploads.
- *Provisioned:* a `chunks` maximum of 10,000 RU/s, billing about 3,000 RU/s in working hours (searches plus uploads) and its 1,000 RU/s floor otherwise, is about $140. `documents` and `libraries` at 1,000 RU/s maximum add about $25. **About $165 a month**, with room to grow.
- *Provisioned is both cheaper and safer.*

**Bulk loads are much cheaper on provisioned throughput.** Loading 1 million chunks is about 144 million RU:

- *Serverless:* about $36.
- *Provisioned:* at a 10,000 RU/s maximum the load takes about 4 hours and adds roughly $5 for those hours. Embedding quota, not Cosmos, sets the actual pace (E29).

Storage is billed the same in both modes, about 9.7 GB per million chunks (E11).

Steady production use can also take reserved-capacity discounts, which serverless cannot.

## Guidance

### When to move from serverless to provisioned

Move when any of these is true:

- searches run steadily through the working day, not in bursts;
- uploads visibly slow searches: 429 responses, or the `usage.totalMs` reported by search rising during loads;
- the organization has more than a few thousand active users, or a library that many agents search all day;
- the deployment needs an SLA, availability zones or a second region;
- the monthly serverless bill exceeds what an autoscale maximum sized for the peak would cost, by the one-eighth rule above.

### How to move

- **Convert in place:** one-way (E22). The data, keys and vector settings stay as they are. Plan it as permanent: a converted account cannot return to serverless.
- **Or deploy a fresh provisioned account** and load it. Choose this when the serverless account should be kept, for example as a demo.
- **Either way, run `scripts/canary.py` against the result before using it** (invariant 11). A deployment that succeeded is not evidence that the index works (E17).

### How to configure production

1. **Autoscale on all three containers, with dynamic autoscale on for the account** (`enablePerRegionPerPartitionAutoscale`). Microsoft recommends dynamic autoscale for every autoscale customer. Each partition then scales and bills on its own, so a high maximum does not mean paying for idle partitions.
2. **Starting maxima:** `spec.md` §11 gives `chunks` 4,000 RU/s and `documents` and `libraries` 1,000 each. Treat 4,000 for `chunks` as the floor of the range, not a target. Two writers alone would use about 3,800 RU/s of it. Start `chunks` at 4,000–10,000 and tune from the normalized RU consumption metric. Raising the maximum later is immediate within the existing partitions.
3. **Give ingestion a throughput budget below the container's maximum.** The worker's write governor caps the number of writes in flight, not RU/s, and each worker process has its own (`src/fli/worker/__init__.py`). `Governor` already supports `budget_per_minute`; production should set it so ingestion uses, for example, at most half of `chunks`' maximum, divided across worker processes. This is what keeps a large upload from crowding out searches. It is not built yet; it is a configuration and wiring change.
4. **Raise `COSMOS_WRITE_CONCURRENCY` with the throughput** (`spec.md` §7.6). The default of 2 suits serverless.
5. **Watch per partition, not only per container.** Use Insights → Throughput → *Normalized RU Consumption by PartitionKeyRangeId*. A partition that sits at 100% while the others are low is a hot library (`untested-at-scale.md`, question 1). Microsoft considers 1–5% of requests answered with 429 healthy when latency is acceptable.
6. **Mind the floors that grow with data.** The lowest autoscale maximum a container can be set to is the largest of: 1,000 RU/s; 10 RU/s per GB stored; one tenth of the highest maximum ever set. At 100 million chunks (about 970 GB), `chunks` cannot go below about 10,000 RU/s maximum, so it bills at least about 1,000 RU/s every hour. Setting a very high maximum once also raises the floor for good.

### Testing on provisioned throughput

The questions in `untested-at-scale.md` (a busy library split across partitions, several partitions in general) can only be tested on provisioned throughput, because serverless cannot be split or given more capacity on demand. That needs a provisioned account:

- **A new small one,** deleted after the test, so it bills only while it exists.
- **Or the demo account converted** (one-way, and the demo stops being free when idle).

Either way it needs the canary, Microsoft's N6 configuration, and authorization for the spend.

## What does not change between modes

- The `chunks`, `documents` and `libraries` definitions in `cosmos/*.json`, including the partition keys, vector shard key, float16 vectors and indexing policy.
- Every rule in `CLAUDE.md`, and every per-operation cost in `evidence.md`.
- Search results and their quality.

The choice of capacity mode is about ceilings, guarantees and price. The data design is the same in both.
