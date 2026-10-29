# Instructions for coding agents

Read `docs/spec.md` before writing code, and `docs/build-plan.md` to see which phase is current. `docs/evidence.md` explains why each rule below exists. Do not relax a rule without reading its evidence entry.

## Invariants: never break these

Each one prevents a failure that was measured, not imagined.

**Queries**

1. **Every vector or hybrid query has `SELECT TOP n`.** Reject query construction without it. (Cosmos may read far more than needed otherwise.)
2. **Every query on `chunks` filters `c.libraryId = @libraryId` by equality on exactly one value.** Never `IN`, `OR` or a join across libraries. Several libraries means several queries. (E4: an `IN` list across shard values cost about 320× more. E5: omitting the value scanned the whole container, about 300× more on real data, and that cost grows with every library's data.) A query ordered by `RANK` (hybrid or full-text) is never sent with a partition key: it comes back unranked, silently (E27). `partition_key_for` in `src/fli/query.py` decides the routing.
3. **The library id in any agent retrieval (search, discovery or complete read) always comes from the URL path the platform called, never from agent-supplied tool arguments.**
4. **Query vectors for the float16 index are converted before use:** round each value to float16, set values with magnitude below 2⁻¹⁴ to 0, and declare `{'dataType': 'float16'}` in `VectorDistance`. Never declare `float32` against this index: it silently turns an indexed search into a full scan (E8). `src/fli/query.py` implements the conversion and the check.
5. **Never select `c.embedding`.**
6. **Leave `filterPriority`, `searchListSizeMultiplier` and RRF weights at their defaults** unless an evaluation run justifies a change (E13).

**Permissions**

7. **The personal-library check (`spec.md` §5 rule 3) runs on every search, document discovery, complete read and agent tool bundle request.** `kind` and `boundAgentId` are immutable: reject any update that touches them.
8. **The index stores no permissions or attachments.** Who may do what, and which agent has which shared library, belongs to the platform (E23).

**Cosmos configuration**

9. **Container definitions live only in `cosmos/*.json`.** Every queried field must be in `includedPaths`. Partition-key and shard-key paths are always included. Never add `/id/?`: Cosmos accepts it with HTTP 202 and then fails the create on the asynchronous operation, which tooling that ignores it sees only as a timeout (E15).
10. **Vector settings are permanent** (E16). Changing dimensions, data type, quantizer, `indexingSearchListSize` or shard key means a new container and a rebuild, never an edit. Keep chunk text and original files so a rebuild is always possible.
11. **Run `scripts/canary.py` against every newly created account or container before using it** (E17).

**Data and secrets**

**Reference-architecture privacy:** repository files, including examples, logs, evidence records, screenshots and generated artifacts, must not identify the operator's test Azure tenant, subscription, accounts, resource groups, endpoints or development computer. Use generic placeholders and role labels. Keep connection settings and local runtime identifiers outside the repository. Before adding live evidence, remove environment identifiers and check for absolute user-machine paths. Never copy a private deny list into the repository.

12. **Do not log query text, chunk text, full-document text or generated descriptions.** Personal libraries hold personal content.
13. **Managed identity only.** No keys, connection strings or secrets in code or config. `AUTH_MODE=dev` must refuse to start unless `ENVIRONMENT=local`.

## Testing

- **Adaptive document retrieval** (`docs/document-retrieval.md`, E34): agents may combine full files and passages in either direction within one turn, without routine user confirmation. Full reads return every selected file or no text with an explicit limitation, never silent truncation. Preserve pre-chunking sections for new ingestion; label legacy indexed text and its overlap. Test mixed evidence, completeness, budgets, metadata enumeration and lifecycle/isolation. Local scripted tests are not evidence of real-model routing quality.

- **Unit tests** cover the query builder, the float16 conversion, the permission rules, chunk-id determinism and the generation check. Make the query-builder checks static: render the SQL and assert on it.
- **The local Cosmos emulator rejects `FullTextScore`** (E21). Test hybrid SQL as text locally. Test its behaviour against a real account in the canary and in integration runs.
- **Never assert an exact list of search results.** Results vary slightly between identical searches, because each replica builds its own index (E14). Assert recall against an exact search instead: `VectorDistance(…, true, …)` forces brute force. That is established for vector-only searches. Whether it works inside a correctly routed `RRF` is untested (E24 withdrawn, E27). Check that hybrid searches rank at all: different questions must get different results.
- **Wait for the vector index before measuring search cost against a live account.** It engages minutes after writes, and until then every search scans (E25).
- **Never use random vectors or filler text as test data for search quality** (E14).

## Conventions

- **Python 3.12 or later** (the code uses 3.12 generic syntax; developed on 3.14). FastAPI for the API. `azure-cosmos` 4.17.1 or later, the version every live run used (E27). `ruff` for lint and format. `pytest` for tests.
- **Errors** are `{"error": {"code", "message"}}`, with the codes named in `spec.md`.
- **Infrastructure** is Bicep, loading container bodies from `cosmos/*.json`.
- **When a spec decision turns out wrong in practice**, change `docs/spec.md` in the same change as the code, and add or update the `docs/evidence.md` entry that justifies it.
