# records/

Canary records written by `scripts/canary.py`, one per run, named `canary-<UTC timestamp>.json`. Commit them: a passing record is the Phase 0 exit check (`docs/build-plan.md`).

Records hold metrics only: request charges, retrieved-document counts, recall, timings, file hashes. They never hold query text, chunk text or file names (CLAUDE.md rule 12).
