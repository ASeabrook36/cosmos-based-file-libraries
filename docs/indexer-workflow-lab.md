# Reproduce the deployed Indexer experiment

This opt-in runbook continues the [direct extraction probes](microsoft-reuse-lab.md).
Read the [measured assessment](consults/2026-10-29-indexer-workflow-experiment.md)
and [record](../records/indexer-workflow-20261028.json) first. The package builder
does not provision resources. Infrastructure, connection binding and the live API
driver require explicit operator configuration; this is not a one-command deployment.
No normal application or demo settings should be changed.

## Private workspace and readiness

Use the normal development environment plus the `reuse-lab` optional dependency.
Set `umask 077`; create an OS temporary directory outside the checkout. Keep all
Azure CLI outputs, runtime parameters, generated packages, raw source responses,
run metadata and receipts there. Do not put environment identifiers in shell
examples, committed records or screenshots. Follow CLAUDE.md's privacy rules.

Use the earlier guide's corpus generator and `inspect-template` command. Pin the
template commit/hash enforced in `scripts/indexer_workflow_lab.py`; do not silently
substitute a newer portal export. Render and inspect the six fixtures. Retain their
private hashes so replacement/replay tests use the same original bytes.

Recheck Entra login, enabled subscription, effective actions/exclusions, role
assignment rights, existing Cosmos vector capability and embedding deployment
capacity. Inspect Web/Logic/Cognitive Services provider registration. Register only
the required providers. Before creating WS1, inspect actual regional usage with:

```sh
az rest --method get --url \
  "https://management.azure.com/subscriptions/$SUBSCRIPTION/providers/Microsoft.Web/locations/$HOST_LOCATION/usages?api-version=2023-12-01" \
  > "$LAB_WORK/web-usage-private.json"
```

Check the applicable Workflow Standard quota and current value, not just
`az appservice list-locations --sku WS1`. Stop on insufficient quota and check
another supported location before provisioning. Recheck model support/quota
separately if exploring KBaaS; this experiment used only an existing embedding
deployment and did not deploy a KBaaS model or dataset.

## Isolated resources and connections

Create a dedicated lab group. Use `infra/indexer-workflow-host.bicep` twice with
private parameter files: first `createHost=false` for storage/UAMI, then upload the
package to its private packages container with Entra (`--auth-mode login`), and
redeploy `createHost=true`. Parameters are storageName, hostName, hostLocation,
storageLocation, operatorPrincipalId and packageBlobName. Use a fresh immutable
package blob name on each update. The host uses UAMI storage/package access; its
separate system identity is used by workflow connections. No account keys,
Azure Files connection strings or SAS package URLs are needed.

Provision a separate Document Intelligence account with local authentication
disabled using `infra/microsoft-reuse-lab.bicep`. Give the workflow system identity
Cognitive Services User there and the existing embedding account's required
OpenAI data role. Give it Blob data access only on lab storage. Record every
assignment outside the lab group privately for cleanup.

At the existing Cosmos account's group, deploy `infra/indexer-workflow-data.bicep`
with a fresh databaseName, accountName and workflowPrincipalId. It creates only
the isolated database and libraries/documents/indexer-lab containers, loading
policies from `cosmos/*.container.json`; its SQL role is database scoped. Match
capacityMode to the account. Grant the local Entra operator the necessary scoped
test data access separately when not already present.

Before using those containers, bind private process settings to **this database**
and run the existing real-text canary:

```sh
python scripts/canary.py --index-container indexer-lab \
  --corpus "$REAL_TEXT_CORPUS" --records-dir "$LAB_WORK/canary"
```

Use at least 1,500 real passages as in the measured run; wait for index engagement.
Keep the full receipts private. Confirm a successful canary before source uploads.

Create Microsoft managed API connections for formrecognizer and, for the baseline
control, documentdb. Configure their managedIdentityAuth parameter sets and
connection accessPolicies for the workflow system principal/tenant. Form Recognizer
requires the DI siteUrl and Cognitive Services audience; documentdb uses the
Cosmos audience. Retrieve connectionRuntimeUrl privately from ARM. Both the runtime
connection authentication and connectionProperties authentication must use the
managed identity. Never use connection keys as a workaround.

For built-in service providers use ManagedServiceIdentity authentication:

| Provider | Required connection values |
|---|---|
| AzureBlob | ManagedServiceIdentity parameter set, blobStorageEndpoint, authProvider.Type |
| OpenAI | openAIEndpoint, authProvider.Type |
| AzureCosmosDB | **accountURI**, authProvider.Type; accountEndpoint is not the tested parameter |

First mount a small Blob smoke workflow and verify an actual successful operation
with storage shared-key access disabled. A healthy site status alone is insufficient.

## Build and verify each variant

Keep a runtime JSON file outside the repository with top-level `workflowName`,
`variant`, `parameters` and `connections`. Variants are baseline, builtin-write and
layout. `parameters` is the Standard workflow parameters.json object with typed
value entries; `connections` is its connections.json object with
serviceProviderConnections and managedApiConnections. Bind every connection name
and parameter referenced in the generated definition. The pinned workflow supplies
the baseline parameter names; the adapter additionally requires
`Lab_Library_<workflowName>` for its fixed trusted library ID and
`cosmos-builtin_<workflowName>` for its built-in writer connection.
CosmosDB_Database/Collection parameters must target the isolated database/indexer-lab.
Use separate input containers/parameter bindings for each variant and scope.

```sh
python -m scripts.indexer_workflow_lab \
  --template "$LAB_WORK/template/workflow.json" \
  --runtime-config "$LAB_WORK/runtime-private.json" \
  --work-dir "$LAB_WORK/package"
```

The output filename is baseline.zip for all variants; the private receipt records
the actual variant. The builder checks source bytes, protects action run history
and validates MI connections. Baseline processing must compare equal after
removing secureData. Control actions reject secureData; Compose accepts secure
inputs and implicitly hides outputs. Keep the builder's tested rules.

Upload a new package blob, update the private package setting and restart the host.
Poll provisioning and runtime status with bounded retries; ARM 202 is not success.
Before uploading source, GET the mounted workflow through the site's ARM path:
`/hostruntime/admin/vfs/site/wwwroot/<workflowName>/workflow.json?api-version=2024-04-01`.
Compare its JSON to the package exactly; also verify workflow health and that it
is enabled. Exported old definitions can survive a package update temporarily.
The runtime management base is
`/hostruntime/runtime/webhooks/workflow/api/management` under that site resource.
Save exports, action status/error metadata and comparison receipts privately.

## Lifecycle and API protocol

Run these steps in order and assert outcomes rather than exact search result lists:

1. **Baseline:** upload all six inputs. Assert read/analyze/chunk/embed success;
   record actual write results. The measured unmodified writer emitted zero rows.
2. **Write bridge:** use builtin-write. Preserve GUID IDs and the Microsoft
   extraction/chunk/embed stages; omit the scalar partitionKey override. Six initial
   inputs produced six rows. Resubmit one trigger history: seven rows but six unique
   text digests. Replace its same-name input: eight rows containing old/new text.
   Delete the input and observe at least 90 seconds: no corresponding row deletion.
   Report these as bridged control results, not successful baseline emissions.
3. **Layout:** create an artifacts Blob container. Use server-owned input names
   `<document-id>/<positive-generation>.<extension>` and a deployment-fixed library
   parameter. For isolation, run a second scope with the same document ID. Two
   workflows are a lab arrangement, not a production recommendation to deploy one
   workflow per library. Seven initial inputs should produce complete row sets and
   seven immutable extraction artifacts bound to exact input bytes.
4. **Replay:** use the original run's trigger name and origin history name:
   POST `<management-base>/workflows/<name>/triggers/<trigger>/histories/<history>/resubmit?api-version=2024-04-01`.
   This is not a runs/id/resubmit operation. Assert stable IDs/checkpoint bytes and
   skipped DI analysis. Embeddings may run again. Cached Blob JSON is decoded from
   `body('Read_cached').content` as a string, not a `$content` binary envelope.
5. **Publication:** collect the full artifact and all rows in the exact library,
   document and generation. Call `scripts.indexer_publication_lab.publish` with
   existing DocumentStore/BlobStore instances, original source bytes and
   expected_previous. The helper validates source binding and complete ordinals,
   freezes the entire provider content and conditionally advances activeGeneration.
   Supply known native image omissions explicitly; do not label missing images as
   successfully copied. See `tests/test_indexer_publication_lab.py` for the callable
   contract and failure cases.
6. **Existing API:** inject the real isolated stores, existing Searcher and real
   embedder into the existing create_app test harness. Use local-only prototype
   authentication. Compare full reads character-for-character to provider content;
   assert tiny multi-document budgets yield no partial source. Exercise wrong-agent
   tools/search/discover/read/images (403), absent cross-library reads (404), and
   library-scoped search before and after lifecycle changes.
7. **Replacement:** upload generation two; confirm old active source is still
   visible before publication. Publish with expected_previous=1; verify exact new
   source and 409 for generation-one source/section references. Overwrite the
   existing revision's input deliberately; assert source_revision_changed and no
   published-source/checkpoint change. Do not present sequential checks as proof
   against concurrent late writes.
8. **Deletion/inventory:** call existing API DELETE, assert source hidden before
   purge, then use the existing worker's purge path. Confirm all target rows and
   artifacts gone and other-library same-ID source intact. Drive existing inventory
   refresh and verify counts/owner description. No extraction worker or generated
   overview is part of this adapter test. Clean input versions separately.

Keep assertions and raw run evidence private when they contain runtime IDs/text.
The durable unit tests exercise the builder/publication contracts; reproducing
live API checks requires binding this protocol to an isolated deployment, not
recovering a previous machine's private driver or settings.

## Checks and cleanup

Run `pytest`, `ruff check .`, `ruff format --check .` and compile both opt-in Bicep
files. Run `scripts/check_reference_privacy.py` with a private deny file containing
all actual account/group/principal/endpoint/runtime-scope identifiers. Publish only
allowlisted aggregate values, limitations and cleanup confirmation. Use `git diff
--check` before handoff.

Remove experiment role assignments on existing OpenAI/Cosmos resources, delete
the isolated Cosmos database, then delete the dedicated group. Deleting the group
alone leaves the database and external assignments behind. Confirm both resources
are absent and both external assignment counts are zero. After identity deletion,
list assignments and filter by principalId locally; `--assignee` may attempt a
Graph lookup for a principal that no longer exists. Confirm deletion completion,
not merely acceptance of the DELETE request. Leave existing models/demo resources
alone. Provider registration may remain; record that separately from running
resource cleanup.
