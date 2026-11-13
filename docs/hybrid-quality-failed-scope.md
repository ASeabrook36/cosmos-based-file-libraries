# Recovery of the failed owned confirmation scope

`scripts/hybrid_quality_failed_scope.py` is an explicit coordinator recovery tool
for a **new, ledger-owned, generation-one Microsoft-profile library** whose normal
background purge cannot inventory chunks. It does not change the application or
the frozen comparison controls. First request ordinary API library deletion and
verify the `deleting` fence. The normal successful-ingestion cleanup remains
`scripts/hybrid_quality_scope.py cleanup`.

The exception here is a bounded set of chunk **point operations**, with each full
`[libraryId, documentId]` partition key derived from immutable generation plans.
There is no SQL inventory, feed scan, shared queue/DLQ drain, provider resubmission,
container-policy change, or resource provisioning. A 500 response is unknown,
never an empty inventory. The tool does not address another scope, generation,
profile, or ambiguous upload.

## Local proof before any cleanup

Keep every input, archive, environment file, output and error receipt private,
outside the repository. The public manifest contains only the already-frozen
source identifiers, filenames, metadata and hashes. A private directory should
contain:

- The durable ownership ledger and unchanged environment from before upload,
  plus the explicit protected-library list.
- The worker management response and its reviewed configuration summary. The
  summary hashes the first container's `image`, `env`, `command` and `args` JSON
  with sorted keys. The deployed code revision must equal the requested revision,
  target the same services, and match the analyzed repository bytes for all eight
  worker/provider/checkpoint/blob/ID files listed by the helper. The management receipt
  is an input attestation; this script does not independently inspect a live image.
- One or more complete archives of the owned library's blob prefix, including
  the original uploads, source-bound provider results, immutable generation plans
  and every observed worker attempt's start/finish checkpoint. Each archive has a
  `blob-manifest-private.json` list of `{blobPath, archiveFile, bytes, sha256}`.
- The final archive also has `library-private.json`, identical
  `documents-before-private.json` / `documents-after-private.json`,
  `blob-paths-after-private.json`, and `capture-receipt.json` with `libraryFenced`,
  `pathsStableDuringCapture`, `documentsStableDuringCapture`, `startedAt`,
  `completedAt`, and `archivedFiles`. Its captured library is already deleting.

All observed starts must match terminal finishes; every attempt that entered
provider/publication work must have reached the deployed worker's completed
chunk-write accounting phase. A source with a plan also needs the matching
source-bound provider result, validated using the production validator. Every
planned text piece and vector must agree. Sources without plans qualify as zero
candidates only when they have a positive, post-fence early-stop record, no
provider/publication attempt or result, and no active generation.

A deleting document may retain `pendingGeneration: 1`: the ordinary deletion
fence deliberately preserves this historical field. That is accepted only with
the matched terminal-attempt and immutable generation-one proofs above. Queued,
processing, changed/replacement or unfenced pending sources block recovery.

The proof closes the **observed owned writer and plan universe**. It is not a
container inventory or an independent provider-management audit. Missing or
ambiguous provider completion remains a blocker. Preserve all checkpoints and
archives when any gate fails.

```sh
# RUNTIME names an existing private directory; setup never creates cloud resources.
sh scripts/dev doctor
.venv/bin/python -m scripts.hybrid_quality_failed_scope build \
  --repository "$PWD" --env "$RUNTIME/api-env.json" \
  --manifest docs/testing/hybrid-quality/manifest.json \
  --ownership "$RUNTIME/final-archive/ownership-before.json" \
  --protected-scope "$RUNTIME/protected-scopes.json" \
  --worker-arm "$RUNTIME/worker-before-private.json" \
  --worker-summary "$RUNTIME/worker-before-summary.json" \
  --worker-revision "$DEPLOYED_WORKER_REVISION" \
  --archive "$RUNTIME/first-archive" --archive "$RUNTIME/final-archive" \
  --output "$RUNTIME/failed-scope-proof"
```

`build` makes no cloud calls. It produces `proof-private.json`, a sanitized
`build-summary.json`, and append-only error events on failure. The output directory
must be new. Review the exact proof, candidate count, helper hash, source hashes,
deployed-code binding and limitations before executing. Use the proof SHA from
the local build summary; do not substitute a hash supplied by an untrusted source.

## Explicit bounded recovery

Announce the cloud action before running. The coordinator must ensure there is
no concurrent ingestion, cleanup or measurement in this owned scope. Keep the
failed original environment unchanged. This is an ordinary-fence-first recovery,
not permission to bypass deletion checks on arbitrary libraries.

```sh
.venv/bin/python -m scripts.hybrid_quality_failed_scope execute \
  --repository "$PWD" --env "$RUNTIME/api-env.json" \
  --proof "$RUNTIME/failed-scope-proof/proof-private.json" \
  --proof-sha256 "$REVIEWED_PROOF_SHA256" \
  --maximum-candidates "$EXACT_REVIEWED_CANDIDATE_COUNT" \
  --late-recheck 60 --timeout 900 \
  --output "$RUNTIME/failed-scope-recovery-attempt-1"
```

The accepted proof hash, exact helper bytes, input hashes, analyzed worker code,
runtime binding, protected set, source identities and current fence must match.
Every retained blob file in every supplied archive is rechecked for its original
private regular-file path, size and hash before any live interaction. Missing,
corrupt or symlink-substituted archives stop recovery while live copies remain.
The live blob paths and contents must still exactly match the final archive.
Before any deletion, every candidate point read must either succeed with the
expected identity/content hash or return an actual 404. Existing chunks are
point-deleted with their observed ETags; an independent SDK client must then see
404 for **every** derived candidate. Any other response stops recovery, retaining
all remaining owned blobs and metadata.

Only after those independent 404s and another unchanged-archive check does the
tool delete each enumerated owned blob, each known owned document and finally the
owned library record. It independently verifies metadata absence and checks the
live owned blob prefix. It repeats the candidate, metadata and prefix checks
30–60 seconds later (default 60). Soft-delete/backup retention is unchanged.
Successful receipts describe this bounded owned universe and make no claim about
unrelated chunks or accounts.

Each mutation attempt and finish is a new fsynced private event file. Failure or
timeout produces `cleanupComplete: false`, preserving every earlier event. Never
reuse an output directory or erase a failed attempt. A partial execution requires
fresh evidence and review; there is no automatic retry/resume that assumes a
failed operation did nothing. If point operations fail because the service itself
is broken, leave the owned library fenced with its original artifacts retained
and report exactly which absence checks remain unknown.

## Local verification

```sh
sh scripts/dev test tests/test_hybrid_quality_failed_scope.py
sh scripts/dev lint
```

Synthetic tests cover provider/plan/source binding, unmatched starts, unfinished
provider/publication work, active sources, fence changes, archived hash tampering,
append-only errors, helper hash changes, missing/corrupt/symlink-substituted
archives (including earlier snapshots), escaped candidates, minimum late-check
duration, pre-delete and independent-verification 500s, late artifacts and the
successful bounded point-delete path. SDKs and time waits are replaced locally;
these tests do not establish live service health or cleanup success.
