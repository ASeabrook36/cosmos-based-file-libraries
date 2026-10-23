# Review response

*2026-10-23. Response to the independent read-only architecture review of commit `566fa70` (Phases 0–2 passed, Phase 3 next). Branch `review-response`.*

The reviewer had no context about the platform that will consume this index. Each finding was checked against the code and documents at `566fa70`, which was still `main` when triage began. Each was then put in one class:

- **Defect:** wrong now, in this prototype.
- **Real risk, later phase:** a correct concern whose impact arrives later.
- **Deliberate, documented:** already chosen, with the reason recorded.
- **Missing evidence:** a claim the repository makes without proof.

Findings that share a root cause are merged. The commits are on this branch, in order.

## Findings

| # | Finding (review severity) | Class | What we did, or why not | Commit |
|---|---|---|---|---|
| 1 | A delete gets stuck when the purge cannot be queued (High) | **Defect**, partly closed | Confirmed. A failed send left the record hidden as `deleting`, and a second `DELETE` returned `404`, so nothing could retry it. `DELETE` is now repeatable: it queues the purge again, and a failed send is `503 queue_unavailable`. **Still open:** a purge that fails on all five deliveries (repeated stale reads, or any other error) dead-letters. It is logged as `purge dead-lettered`, but nothing yet alerts on it or retries it, so its library or document stays hidden and unpurged until someone repeats the `DELETE`. The follow-up is the dead-letter alerting and purge-verification job in `build-plan.md` Phase 5, item 6. An upload whose record create fails now removes its blob. A failed queue send after upload was already handled: the document becomes `failed` and is deletable. | `2d44363` |
| 1b | *Found in triage:* a purge completes without purging after a stale read | **Defect** (documented semantics; not observed) | The API and the worker use separate Cosmos clients. Under Session consistency, a client reads another's writes with Eventual consistency. A worker that read the record before `deleting` reached its replica used to complete the purge doing nothing (`purgeLibrary` logged "ignored"). Purges queued by a deletion now carry `deleted: true`, and the worker retries until it sees `deleting`. `evidence.md` E28. | `2d44363` |
| 2a | A long heading inflates chunks (Medium) | **Defect**, worse than reported | Measured: a 520-token heading over 1,800 tokens of text made 1,650 chunks, not one oversized chunk. `chunking-v1.1` carries at most 64 tokens of heading and puts the full heading at the start of the body. The same input now makes 7 chunks. Headings of 64 tokens or fewer chunk as before. `evidence.md` E29. | `9c85c4b` |
| 2b | Ingestion memory and work are unbounded (Medium) | **Defect** (cap) and **real risk, Phase 5** (batching) | `INGEST_MAX_CHUNKS_PER_DOCUMENT`, default 5,000, refuses a larger document as `too_many_chunks` before embedding. A 50,000-chunk document would outlast the worker's one-hour session lock, starve other libraries' writes and hold about 5 GB of vectors. Embedding and writing in bounded batches is Phase 5. `evidence.md` E29. | `9c85c4b`, `4dd1e54` |
| 3 | `usage.requestCharge` leaves out some Cosmos calls (Medium) | **Defect** | Confirmed: the library read and the generation check's document reads were not metered. Now every Cosmos call in a search is, and a test pins the sum with known per-call charges. Request charges reported for Phases 1 and 2 exclude these reads (about 1 RU each); `build-plan.md` says so. | `f357f55` |
| 4 | Lists have no pagination (Medium) | **Real risk**, Phases 4–5 | Confirmed, and not a problem at demo scale. *Found in triage:* `refresh_library` reads every document record after each ingest, so a bulk load into one library is quadratic. Phase 4 measures both; Phase 5 adds continuation tokens and incremental stats. | `4dd1e54` |
| 5 | Visibility across Cosmos clients is not demonstrated (Medium) | 1b is a **defect** (fixed); the rest is **missing evidence** | Confirmed against Microsoft's consistency documentation. Within one API process, a search sees that process's deletes. Across API instances it is unproven: `evidence.md` N13, and a Phase 5 test before more than one instance runs. `spec.md` §5 rule 5 is qualified. | `4dd1e54` |
| 6 | Generation filtering can underfill results (Medium) | **Real risk**, Phase 3 | Confirmed. Today it can happen briefly, while a document is being written or purged. A purge that never ran (finding 1) would have made it permanent. It becomes likely with replacement, so it is a Phase 3 exit check. | `4dd1e54` |
| 7 | Cosmos backup has no Blob recovery to match (Medium) | **Real risk**, Phase 5, partly closed now | Blob soft delete is now 7 days, matching Cosmos's `Continuous7Days`, so originals and records restore together (`spec.md` §3, §7.8; `evidence.md` E16). It was applied to the demo account on 2026-10-23. *Found in triage:* `spec.md` §7.8 promises "a new blob version" on replacement, but versioning is off and the blob path is keyed by file name. A same-name replacement would overwrite the original still being served, so Phase 3 gives each generation its own original. The restore drill stays in Phase 5. | `c8c7088`, `4dd1e54` |
| 8 | The SDK range is wider than the evidence (Low–Medium) | **Defect** | `azure-cosmos` floor raised from 4.9 to 4.17.1, the version every live run used (`evidence.md` E27). The canary's `hybrid_ranks` check catches ranking changes on live runs. | `c8c7088` |
| 9 | The spec's status line is out of date | **Defect** (documentation) | `spec.md` now says Phases 0–2 passed. | `c8c7088` |
| 10 | Production gates: vector search with hierarchical keys, float16, spherical, DiskANN at scale, one partition, Entra, public network, hybrid cost | **Deliberate, documented** | Already recorded (`evidence.md` N1, N4–N6, N10, N11; E27; `build-plan.md` Phase 5). The review gives no reason to revisit them. Public network access is now an explicit Phase 5 decision. | `4dd1e54` |
| 11 | No CI workflow | **Missing** | `.github/workflows/ci.yml` runs ruff and the unit tests on Python 3.12 (the floor) and 3.14 (development), with no secrets and no Azure. | the commit that adds this file |

## Closed in Phase 3

The Phase 3 items from findings 1, 6 and 7 are built and unit-tested (`build-plan.md` Phase 3, Lifecycle). Their live check, `scripts/phase3_exit.py lifecycle`, has not run.

- **Finding 1, redelivered ingest.** Any ingest whose generation is no longer pending queues the purge again, so a purge send that failed after a commit is retried by the redelivery (`spec.md` §7.1). The dead-lettered purge below is unchanged.
- **Finding 6, underfill.** Decided: report, not refetch. Coverage says how many passages were left out (`spec.md` §8 step 6; `evidence.md` E30).
- **Finding 7, originals.** Each generation's original has its own path, and the purge deletes obsolete ones (`spec.md` §4.2, §7.8; E30).
- *Found in planning:* without a counter a generation number could be reused after a failed replacement, exposing that failed file's leftover chunks; and a stale read could lose a replacement. The record's `generation` only increases, and ingest retries a stale read (E28, E30).

## Open follow-up

- **Deletion recovery is not fully closed (finding 1).** A purge that dead-letters is logged, but not alerted on or retried. Until Phase 5's dead-letter alerting and purge-verification job exist, someone must notice the log line and repeat the `DELETE`. The reviewer re-checked `main` at `8aab5e7` and asked for this to stay visible as an operational follow-up.

## Also changed

- **Python version.** The documents said "Python 3.12", which read as a pin. It is a floor: the code uses 3.12 generic syntax. Development, and every result in this response, ran on 3.14. The documents now say "3.12 or later".

## What was verified, and against what

- **Tests.** 282 unit tests pass and ruff is clean at every commit, on Python 3.14.3 locally. Python 3.12 is covered only once CI has run.
- **External claims.** The review verified no Microsoft documentation, and said so. The Session-consistency behaviour behind E28 and N13 was checked against Microsoft Learn, "Consistency level choices" (updated 2026-04-27). That `raw_response_hook` meters point reads was checked in the installed `azure-cosmos` 4.17.1 source.
- **Chunk counts.** The figures in finding 2a were measured locally with the `cl100k_base` tokenizer. No Azure was involved.
- **Not tested live:** the stale-read retry (1b), the full search charge (3) and soft delete during a real purge. Each is covered by unit tests or by configuration only.
