# Mounted workflow restart binding

`scripts/hybrid_quality_workflow.py` supplies the independently reopened workflow
evidence required before another full-corpus ingestion. It changes neither the
workflow nor the frozen application, recipe, query, model or benchmark controls.
Only the coordinator runs its cloud command, after announcing the read-only step.

The helper makes at most twelve ARM GETs, with no HTTP retries, a 20-second bound
per GET and a 300-second overall deadline. It reads the existing worker and its
latest ready revision, the actual embedding account/deployment, workflow host and
authentication configuration, all five mounted package files, and the referenced
Document Intelligence connection. It never invokes the workflow, starts a worker,
uploads a source, requests embeddings or modifies a resource.

Keep a private JSON configuration outside every checkout. Its `subscription` is
the already reviewed subscription and `inputs` maps these names to private files:

- `environment`: the coordinator's unchanged API settings mapping;
- `workerBefore`: the retained worker ARM response body;
- `embeddingBefore`: the retained `{capturedAt, account, deployment}` envelope;
- `modelBindings`: the existing mapping with `embedding.url`;
- `workflowHostBefore`, `workflowDIBefore`: retained ARM envelopes with `body`;
- `priorBinding`: the independently reviewed M1 mounted-file hash mapping;
- `expectedPackage`, `packageReceipt`: the reviewed ZIP and its exact
  `packageSha256` receipt.

```sh
.venv/bin/python -m scripts.hybrid_quality_workflow \
  --config "$HQ_PRIVATE/workflow-config.json" \
  --output "$HQ_PRIVATE/workflow-before"
```

The output directory must be new. `workflow-binding-private.json` passes only
when every required check passes. Actual mounted JSON must equal every reviewed
ZIP member and every M1 mounted-file hash. The reader separately retains exact
HTTP response bytes. Mounted canonical hashes use sorted compact JSON with
default ASCII escaping, matching M1; they are distinct from the ZIP byte hash
and from the general `structure_lab.digest` convention.

The worker must have an immutable image digest, unchanged configuration and a
healthy active ready revision matching its template. The frozen settings parser
resolves an omitted embedding-dimension variable to 3072; both its source hash
and the ingestion recipe source hash must match the pinned foundation. Mounted
parameters must bind the same source container, managed identities and actual
embedding deployment/revision/dimensions. Raw and input artifacts, settings,
producer source and the actual endpoint are hash-bound for the restart gate.

This capture verifies authentication configuration, not fresh runtime denial
behavior. It binds Document Intelligence action/model/API parameters, not a
provider implementation build. Configuration captures are sequential rather than
a transactional multi-service snapshot. A failed check stops ingestion and is
retained as evidence; it does not authorize editing the frozen package or
rerunning benchmark questions.

Local checks use no cloud calls:

```sh
sh scripts/dev doctor
.venv/bin/pytest -q tests/test_hybrid_quality_workflow.py
.venv/bin/ruff check scripts/hybrid_quality_workflow.py tests/test_hybrid_quality_workflow.py
.venv/bin/ruff format --check scripts/hybrid_quality_workflow.py tests/test_hybrid_quality_workflow.py
```
