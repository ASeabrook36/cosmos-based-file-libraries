# records/

Canary records written by `scripts/canary.py`, one per run, named `canary-<UTC timestamp>.json`. Commit them: a passing record is the Phase 0 exit check (`docs/build-plan.md`).

Phase 1 exit-check records written by `scripts/phase1_exit.py`, named `phase1-exit-<UTC timestamp>.json`:

- `phase1-exit-20261023T012726Z.json`: ingestion of 20 documents passed; every search returned the same unranked results, because hybrid queries were routed by partition key (`docs/evidence.md` E27).
- `phase1-exit-20261023T013913Z.json`: the same library searched again, vector-only, after the fix. A resumed run: it reuses the library and carries no ingestion timings.

Records hold metrics only: request charges, retrieved-document counts, recall, timings, file hashes. They never hold query text, chunk text or file names (CLAUDE.md rule 12).
