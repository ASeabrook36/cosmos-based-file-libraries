# Fresh-health authorization before confirmation ingestion

The comparison's frozen sources, questions, ranking implementation, representation
and answering controls are unchanged. The coordinator now has an executable gate
for the restart prerequisites identified in the cross-session review.
`scripts/hybrid_quality_restart.py` only reads private evidence. It neither
provisions resources nor performs a health check by itself.

Before `hybrid_quality_scope ingest` can create a library or upload another source,
provide `--restart-preflight` naming a private `fli.hybrid-quality.restart.v1`
receipt. Its envelope contains:

- `evaluationStateDir`: the exact private `--state-dir` for this ingestion;
- `settingsBinding`: the complete mapping returned by `hybrid_quality_scope.binding`;
- `targetFingerprint`, `canarySha256` and sorted `protectedLibraryIds`, identical
  to the actual-container preflight, passing canary and protected inventory;
- `completedAt`: a timezone-aware timestamp after the smoke and workflow receipts;
- `healthReceipt`, `workflowReceipt`, `partialCleanupPlan`: each a
  `{ "path": "<private absolute file>", "sha256": "<exact bytes SHA-256>" }` reference.

The gate reopens every referenced artifact, including raw receipts, actual mounted
workflow files, smoke originals and the owned smoke inventory. Paths resolve
outside every Git checkout. Hash mismatches fail closed. Hashes provide artifact
integrity, not independent certification of the coordinator; the reviewed live
helpers and independent evidence review remain necessary.

The health producer is `scripts/hybrid_quality_smoke.py`. Its finalized
`fli.hybrid-quality.restart-health.v1` receipt must have `status: passed` and bind
its actual producer source hash. It uses a separately owned smoke library and
real sources whose original hashes differ from every frozen confirmation source.
The reopened ownership ledger must contain one library, one to twelve documents
and exactly twelve unique first-generation chunks with text and 3,072-dimensional
vectors. All rows belong to the same smoke owner and library. The attempted write
inventory must match those exact rows.

Required smoke outcomes are complete writes and matching point reads for every
owned row; a scoped query returning exactly twelve chunks; two vector queries
using the library prefix and two hybrid queries without an explicit partition
key; three scoped results per query, with different hybrid lists. Cleanup must
record every owned row deleted, actual point-read 404s and an empty scoped result,
then repeat absence checks after 30–60 seconds. Failed reads are not absence.
Fresh actual embedding deployment receipts must bracket the writes and cleanup,
bind the configured endpoint and deployment, and preserve model/revision/SKU.
The smoke establishes working operations, not index engagement, semantic recall,
steady-state latency or production lifecycle acceptance.

The workflow producer is `scripts/hybrid_quality_workflow.py`. Its complete
`fli.hybrid-quality.workflow-binding.v1` receipt binds the actual worker's ready
revision, immutable image, configuration and data targets; the invoked endpoint;
mounted package contents and parameters against the reviewed package and prior
M1 files; authentication, managed identities and actual embedding deployment;
and the frozen Microsoft profile recipe. All defined checks must pass, the
producer and recipe source hashes must match, and raw/mounted files are reopened.
Matching an old worker image or endpoint alone is insufficient.

The separate `fli.hybrid-quality.partial-cleanup.v1` declaration names the same
state directory and future ownership ledger, settings, target, protected inventory
and frozen manifest hash. It permits at most one evaluation library and binds the
actual ordinary-cleanup and failed-scope helper hashes. Its explicit policy must
require archives before fencing, terminal writers, immutable generation plans,
current state matching archives, all candidate reads before any delete, ETag
conditions, independent 404s before blob deletion, late checks and retained
artifacts on incomplete recovery. Unknown state stops recovery. Automatic retry,
provisioning, provider resubmission and shared queue draining are forbidden;
ordinary soft-delete/backup retention stays in force. The declaration is a
precondition, not proof that later cleanup succeeded. A failed ingestion still
needs newly captured evidence and a separately reviewed recovery proof.

Every health/workflow timestamp and model readback must be at most one hour old
when authorizing new mutations. `--restart-max-age` can tighten that bound to
60–3,600 seconds; its default is 3,600. The gate rejects future or reversed
timestamps and requires the final envelope to postdate its evidence. It persists
the accepted receipt hash, component hashes and acceptance time in the ownership
ledger before API startup. A paused partial upload needs fresh evidence before
another upload. Ambiguous create/upload responses remain non-retriable even with
fresh evidence, and a library already fenced for deletion cannot resume ingestion.

For the existing owned-scope command, add:

```sh
.venv/bin/python -m scripts.hybrid_quality_scope ingest \
  --state-dir "$HYBRID_PRIVATE/owned-scope" \
  --env "$HYBRID_PRIVATE/api-env.json" \
  --preflight "$HYBRID_PRIVATE/infrastructure-preflight.json" \
  --canary "$HYBRID_CANARY_PRIVATE" \
  --protected-scope "$HYBRID_PRIVATE/protected-scopes.json" \
  --source-dir "$HYBRID_CORPUS_PRIVATE/sources" \
  --restart-preflight "$HYBRID_PRIVATE/restart-preflight.json"
```

A historical inherited-canary receipt alone no longer authorizes new uploads.
A new container still requires the full existing canary in addition to this gate.
`status`, ordinary `cleanup`, and polling after all upload responses are recorded
do not require fresh restart evidence, so expiry cannot strand historical scopes.
The existing ownership, identity, generation, source-hash and live-container checks
continue to apply to those actions.

Local verification (no cloud or credentials):

```sh
.venv/bin/pytest -q tests/test_hybrid_quality_restart.py tests/test_hybrid_quality_scope.py
.venv/bin/ruff check scripts/hybrid_quality_restart.py scripts/hybrid_quality_scope.py tests/test_hybrid_quality_restart.py
.venv/bin/ruff format --check scripts/hybrid_quality_restart.py scripts/hybrid_quality_scope.py tests/test_hybrid_quality_restart.py
```
