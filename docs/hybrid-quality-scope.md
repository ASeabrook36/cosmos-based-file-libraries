# Reproducible owned confirmation scope

`scripts/hybrid_quality_scope.py` is an explicit coordinator-run cloud command for
one new shared library using the frozen `microsoft-indexer-layout-v1` profile.
It does not provision infrastructure, deploy or start workers, change models,
search, answer questions, or alter a pre-existing library. Announce the cloud step
before invoking it, use the coordinator's isolated canary-validated services, and
keep ingestion outside all scheduled performance windows.

The source [manifest and gold](hybrid-quality-corpus.md) must verify locally first.
This helper verifies them again before any upload. It retains original published
filenames, original bytes, hashes and metadata. `ingest` creates one new library,
uploads the entire manifest inventory once, and polls until all eight sources are
ready at generation 1 with no pending generation. It does not use a filename glob,
so uppercase `.DOCX` downloads are included. A local API instance uses the supplied
private settings and the existing authorized worker processes the queue normally.

Before new ingestion mutations, the [fresh-health restart gate](hybrid-quality-restart.md)
requires current owned smoke, mounted-workflow and partial-cleanup receipts.
An inherited canary alone cannot authorize another upload. Status, cleanup and
pure polling after every upload acknowledgement remain available without fresh
restart evidence.

Before any request, the helper checks the actual three container policies and
binds the chunks-container incarnation to the passing canary and coordinator
preflight. It requires local dev authentication, 3072-dimensional embedding
revision 1 and fixed top limits 8/20. Runtime settings, the manifest and the full
protected-library inventory are bound to the private ownership ledger. Changing
those bindings cannot silently resume a different experiment.

Prepare these outside every Git checkout:

- `api-env.json`: the coordinator's string environment mapping;
- `infrastructure-preflight.json`: existing infrastructure envelope with
  `inheritedCanaryPassed`, `incarnationAndPoliciesMatch`, `newContainerCreated:false`,
  `scaleResourcesAccessed:false`, `canarySha256` and `targetFingerprint`;
- the passing inherited canary JSON matching that hash and incarnation;
- `protected-scopes.json`: `{"libraryIds": ["<each protected library ID>"]}`.
  Include every frozen R0, P1, I1 and unrelated library identified by the
  coordinator, not merely the current corpus.

For example, with private shell variables already set by the coordinator:

```sh
.venv/bin/python -m scripts.hybrid_quality_scope ingest \
  --state-dir "$HYBRID_PRIVATE/owned-scope" \
  --env "$HYBRID_PRIVATE/api-env.json" \
  --preflight "$HYBRID_PRIVATE/infrastructure-preflight.json" \
  --canary "$HYBRID_CANARY_PRIVATE" \
  --protected-scope "$HYBRID_PRIVATE/protected-scopes.json" \
  --source-dir "$HYBRID_CORPUS_PRIVATE/sources" \
  --restart-preflight "$HYBRID_PRIVATE/restart-preflight.json" \
  --timeout 1800 --poll-interval 10
```

Keep stdout/stderr in private runtime logs. The final JSON output and
`ingestion-summary.json` contain public source roles, ready-document counts,
per-source **published document chunk counts**, and whitelisted numeric provider
usage. These are actual publication metrics rather than the corpus token estimate;
the direct-measurement tool independently verifies the actual chunk inventory.
Null provider usage stays unavailable, never zero. Generation totals are not
complete per-attempt billing receipts; retain worker attempt records separately.
No automatic claim about missing provider cost is made.

`evaluation-library.json` contains the private library ID and ready document
snapshot for the measurement/replay coordinator. `ownership.json`, HTTP replies,
full document records and failure details contain runtime IDs and remain private.
Do not commit them. Only reviewed sanitized summaries may enter results records.

An OS lock prevents simultaneous commands against one ledger. State is written
atomically and fsynced **before** create and upload requests. A lost response leaves
an attempted operation without a trusted result; resuming stops rather than
creating a second library or uploading a second copy. The helper does not adopt
resources by guessing names or repair an ambiguous ledger automatically. The
coordinator must inspect private service receipts and resolve ownership explicitly.
Successful uploads are never repeated. Pending ingestion can be resumed with the
same `ingest` command and state directory. Failed sources are a blocker requiring
coordinator diagnosis, not automatic retry with new source identities.

`status` uses the same flags (source directory is optional) and makes no mutations.
Do not run it concurrently with the same ledger's ingest operation; the latter
writes `ingestion-summary.json` and `documents-private.json` on every poll.

After final direct/replay receipts are durable and accepted, announce cleanup and
run the same command with action `cleanup`, keeping every scope binding unchanged:

```sh
.venv/bin/python -m scripts.hybrid_quality_scope cleanup \
  --state-dir "$HYBRID_PRIVATE/owned-scope" \
  --env "$HYBRID_PRIVATE/api-env.json" \
  --preflight "$HYBRID_PRIVATE/infrastructure-preflight.json" \
  --canary "$HYBRID_CANARY_PRIVATE" \
  --protected-scope "$HYBRID_PRIVATE/protected-scopes.json" \
  --timeout 300 --poll-interval 5 --late-recheck 30
```

Cleanup requires recorded successful ingestion and rechecks all sources are ready,
unchanged, and ledger-owned before the first delete. It sends only the ordinary
library DELETE to that single ledger-owned library, whose ID must not be in the
protected inventory. Ordinary DELETE can be retried using the same ledger; no
wildcard storage purge, direct database delete or protected-scope mutation exists.
It polls SDK library/document records, one bounded library-scoped chunk existence
projection and the complete live blob prefix until absent. It then waits and
repeats all four checks to catch late artifacts. A late artifact fails cleanup;
it is never silently ignored or broadly deleted. The sanitized cleanup summary
reports live-namespace absence only; configured soft-delete and backup retention
remain intact.

The coordinator is responsible for deciding when final receipts are complete;
the helper does not infer that from source readiness. A still-running, failed or
ambiguous ingestion does not qualify for this cleanup path. Keep its private
ownership evidence and investigate the unresolved gate. The helper never modifies
or repairs unrelated resources to make a check pass.

Local verification uses no cloud calls:

```sh
.venv/bin/pytest -q tests/test_hybrid_quality_scope.py
.venv/bin/ruff check scripts/hybrid_quality_scope.py tests/test_hybrid_quality_scope.py
.venv/bin/ruff format --check scripts/hybrid_quality_scope.py tests/test_hybrid_quality_scope.py
```

The tests cover lost responses, durable pre-attempt ownership, idempotent resume,
protected libraries, altered metadata, changed runtime bindings, late artifacts,
and a real API/worker/ordinary-delete rehearsal with in-memory services. Actual
cloud ingestion, provider usage completeness and live cleanup remain coordinator
execution gates until their private and sanitized receipts exist.
