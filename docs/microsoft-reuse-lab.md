# Reproduce the Microsoft reuse probes

For the subsequent deployed workflow, connector, publication and lifecycle tests,
use the [Indexer workflow runbook](indexer-workflow-lab.md) and
[completed assessment](consults/2026-10-29-indexer-workflow-experiment.md).
The instructions below reproduce the earlier direct extraction comparison.

This opt-in experiment inspects a pinned Microsoft Document Indexer template and
compares its extraction model with Document Intelligence layout. It does not run a
deployed Indexer or KBaaS workflow. Read the [assessment](consults/2026-10-28-microsoft-reuse-compatibility.md)
and [sanitized record](../records/microsoft-reuse-20261028.json) for measured failures
and unverified gates. No normal API, worker or demo command changes.

## Private setup and Azure readiness

Install the normal development dependencies and optional `reuse-lab` dependencies
in an isolated Python environment. Runtime files must be outside the repository:

```sh
umask 077
LAB_WORK="$(mktemp -d)"
python -m scripts.microsoft_reuse_corpus --work-dir "$LAB_WORK/corpus"
python -m scripts.microsoft_reuse_lab inspect-template --work-dir "$LAB_WORK/probes"
```

Record the corpus checksums and reportlab/Pillow versions in private experiment
notes. New PDFs use ReportLab invariant mode for reproducible bytes within those
versions. The baseline run predates invariant mode; its original bytes remain in
the private snapshot. The DOCX is copied unchanged from the checked-in review
fixture. Render the PDFs with Poppler and inspect all pages before live calls.

Before provisioning, use the existing Azure CLI Entra login to recheck subscription
state, effective permission scopes and excluded actions, regional service capacity,
resource-provider registration and applicable role-assignment rights. Capture full
outputs privately, including `az account show`, subscription
`Microsoft.Authorization/permissions`, and Cognitive Services provider state.
For a future workflow test, additionally inspect `az appservice list-locations
--sku WS1`, Web/Logic provider state, agent model support and available model quota.
Quota and region listings do not guarantee deployment success.

Create a fresh resource group used only for these extraction probes. Choose its
name, location, a globally unique account name and operator principal outside this
checkout. Do not use the demo resource group. The opt-in Bicep resource has no
account keys and grants Cognitive Services User to the local operator:

```sh
az group create --name "$LAB_GROUP" --location "$LAB_LOCATION" \
  --tags purpose=bounded-microsoft-reuse-experiment > "$LAB_WORK/group-private.json"
az deployment group create --resource-group "$LAB_GROUP" \
  --template-file infra/microsoft-reuse-lab.bicep \
  --parameters accountName="$LAB_ACCOUNT" operatorPrincipalId="$LAB_OPERATOR_ID" \
  > "$LAB_WORK/deployment-private.json"
```

Recover the endpoint from that private deployment output into the private environment
variable `MICROSOFT_REUSE_DOCINTEL_ENDPOINT`. The driver intentionally does not use
the application's `DOCINTEL_ENDPOINT`. Local probes use Azure CLI credentials; a
hosted adapter must use managed identity. Allow time for role propagation. No new
Cosmos container is needed for these extraction-only probes. A future workflow
container must have its definition in `cosmos/` and pass the standard canary first.

## Live probes and offline replay

```sh
python -m scripts.microsoft_reuse_lab analyze \
  --manifest "$LAB_WORK/corpus/manifest.json" --work-dir "$LAB_WORK/probes"
```

The manifest allows at most six documents, 20 MiB each. PDFs over the configured
page budget (default and maximum 12) are refused before a call; they are never
silently prefix-read. DOCX page units are provider-reported, not locally predicted.
Each file gets a read and layout call, with a 180-second analysis deadline. The
driver prints only case labels, model names and status. Source responses, errors
and receipts are private files with restrictive modes. Do not commit them.

Inspect private `extraction-report.json`. `acceptancePassed` checks literal text
probes and reference validity only. `sectionDiagnostics` independently records
whether the provider's section spans cover expected authored regions: a successful
analysis or process exit is **not** a successful chapter-completeness test. In the
baseline, Layout's Chapter 1 selection failed despite valid references. Read does
not return a section graph. Never relabel these diagnostics as a served source API.

Repeating the command verifies cached response hashes, reevaluates current probes
and performs zero new analysis calls for successful cached extractions. Input,
endpoint, model, API version and option changes require a fresh work directory;
changing the test assertions can reuse the raw response. Failed calls can be retried
explicitly by rerunning. The driver does not claim retry/publication idempotence for
Document Indexer. Use a single writer for each private directory.

To test actual figure-link resolution, make a private manifest containing only the
nested-report case, keeping its path relative to that manifest, then run:

```sh
python -m scripts.microsoft_reuse_lab figure-probe \
  --manifest "$LAB_WORK/corpus/figure-manifest.json" --work-dir "$LAB_WORK/figure-probe"
```

This makes one layout call and downloads one to eight returned figures through the
existing endpoint-bound client. Inspect the PNG and its separate caption in the
private response. Successful cached runs do not repeat the GET. A figure asset does
not establish preservation through the workflow's text-only projection.

Run `pytest -q tests/test_microsoft_reuse_lab.py`, repository lint/format checks and
the normal suite. Before publishing aggregate results, use an explicit field
allowlist, remove configuration/endpoint information, and run
`scripts/check_reference_privacy.py` with a private deny file covering both the
existing environment and newly created lab identities. Never copy the deny file
into the checkout.

## Cleanup and remaining experiment

Verify the dedicated group's purpose tag and resource inventory against the private
creation receipt before deleting it. Delete only that group, then check deletion
completion. Keep its private receipt until completion is confirmed. A request
accepted for deletion is not proof that cleanup has finished.

```sh
az group delete --name "$LAB_GROUP" --yes --no-wait
az group exists --name "$LAB_GROUP"
```

This runbook supplies no KBaaS query endpoint because a supported external evidence
contract has not been established. Do not invent one from management URLs or bind
the application to internal tables. The assessment lists the remaining deployed
workflow, lifecycle, isolation, source-read and overview-refresh tests. Their
absence is an explicit limit of this experiment.
