# Python / .NET 3.63.0 client comparison v1

This is a new exploratory experiment on the completed `8f7d436` foundation. It
preserves the consumed eight-original / 48-question pack, representation,
query-vector cache, application defaults and completed evidence. It does not
establish fresh confirmation evidence. David Ames's exact SQL, request options
and partition layout are unknown: the SDK comparison is controlled, but is not
a full replication of his setup.

The historical Python failure and routing cost are distinct hypotheses. E27 and
E56 found library-prefix hybrid queries returning query-independent rows, while
the WHERE-only workaround used a query plan with higher request charges. The
[.NET non-streaming ORDER BY fix](https://github.com/Azure/azure-cosmos-dotnet-v3/pull/5273)
addresses passthrough ranking behavior; it does not prove efficient library
routing. [3.63.0 release notes](https://github.com/Azure/azure-cosmos-dotnet-v3/blob/3.63.0/changelog.md)
identify the exact SDK release. The NuGet lock pins that version, and explicitly
pins Newtonsoft.Json 13.0.3 rather than taking the SDK's old default dependency.

## Frozen matrix

| SDK | Query | Request routing | Measured queries |
| --- | --- | --- | ---: |
| Python azure-cosmos 4.17.1 | Vector | Library prefix | 96 |
| Python azure-cosmos 4.17.1 | Hybrid | Library prefix, diagnostic exception | 96 |
| Python azure-cosmos 4.17.1 | Hybrid | WHERE only | 96 |
| .NET Microsoft.Azure.Cosmos 3.63.0 | Vector | Library prefix | 96 |
| .NET Microsoft.Azure.Cosmos 3.63.0 | Hybrid | Library prefix | 96 |
| .NET Microsoft.Azure.Cosmos 3.63.0 | Hybrid | WHERE only | 96 |

Each question gets every cell twice. Cell order rotates across questions and is
reversed in repeat two. There are six separate warmups and 384 separate exact
vector reference queries (48 queries times eight complete document partitions).
All operations are serialized; the coordinator must exclude concurrent load.
No new embedding or answering-model calls occur.

The application query builder supplies byte-identical SQL and parameter objects
to both clients: TOP16 candidates, the same projection, float16 rounding and
subnormal flush, exact library equality, lexical tokenizer, and default RRF
weights/full-text score scope. No selector, evidence packaging or representation
change occurs. Results are the raw ranked candidate list before API post-filter,
neighbor expansion or selector processing. Document-coverage measures therefore
differ from final answer, passage or fact recall.

Both SDKs use Gateway, one logical query at a time, one HTTP connection per host,
16 requested items per page, no application retries and zero configured throttle
retries. Python connection retries are also zero. .NET MaxConcurrency is 1 and
MaxBufferedItemCount is 16; Python's query pipeline remains its pinned default.
SDK-native query planning, metadata refresh, retries and caching remain measured
client differences, not asserted equivalent. The same fixed bearer token is used
by both clients; it must last the entire 1,800-second run plus 120 seconds.

## Local installation and checks

Run `sh scripts/dev setup` in this worktree for the isolated, locked Python
installation. Keep the official .NET SDK 10.0.403 installation, CLI home, NuGet
package cache and all raw experiment files outside Git. Install that exact SDK
using Microsoft's official `dotnet-install.sh --version 10.0.403 --install-dir`
with a private directory. Set the following to private absolute directories;
`dotnet` below must resolve to that installed executable:

```sh
export DOTNET_ROOT=/absolute/private/dotnet
export DOTNET_CLI_HOME=/absolute/private/dotnet-home
export NUGET_PACKAGES=/absolute/private/nuget-packages
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_GENERATE_ASPNET_CERTIFICATE=false
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
"$DOTNET_ROOT/dotnet" restore tools/cosmos-sdk-probe/CosmosSdkProbe.csproj --locked-mode
"$DOTNET_ROOT/dotnet" build tools/cosmos-sdk-probe/CosmosSdkProbe.csproj --no-restore -c Release
"$DOTNET_ROOT/dotnet" tools/cosmos-sdk-probe/bin/Release/net10.0/CosmosSdkProbe.dll --self-test
.venv/bin/python -m pytest tests/test_hybrid_sdk_comparison.py -q
.venv/bin/ruff check scripts/hybrid_sdk_comparison.py tests/test_hybrid_sdk_comparison.py
.venv/bin/ruff format --check scripts/hybrid_sdk_comparison.py tests/test_hybrid_sdk_comparison.py
```

These commands install/restore dependencies and perform local validation only.
No cloud provisioning, credentials, deployment, ingestion or measurement is
part of automatic environment setup. The SDK's loaded assembly version/hash,
DLL hash, source hash, package lock and Python version are preserved at runtime.

## Coordinator input contract and gates

Only `run` calls Cosmos. Before it, the coordinator announces the cloud window,
checks fresh service health, completes an isolated owned-scope ingest and live
lifecycle checks, and captures the new library after all documents are ready.
Keep all paths, identifiers, source text, credentials and raw wire captures in a
new private directory outside every Git worktree. Do not use the old cleaned-up
library or any protected/ongoing scaling resource.

Create a private configuration JSON with this shape (placeholders are not usable
service identifiers):

```json
{
  "schema": "fli.hybrid-sdk-comparison.v1",
  "endpoint": "https://owned-account.invalid/",
  "database": "owned-database",
  "libraryId": "new-owned-library",
  "targetFingerprint": "sha256-from-current-container-incarnation",
  "sourceSnapshot": "/absolute/private/source-snapshot.json",
  "ownership": "/absolute/private/owned-scope.json",
  "protectedScopes": "/absolute/private/protected-scopes.json",
  "queryCache": "/absolute/private/frozen-query-cache.json",
  "healthReceipt": "/absolute/private/fresh-health-receipt.json",
  "snapshotCapturedAt": "ISO-8601 UTC timestamp no more than one hour old",
  "ownedScopeReady": true,
  "freshHealthAccepted": true,
  "noOverlappingMeasurements": true,
  "dotnetExecutable": "/absolute/private/dotnet/dotnet",
  "dotnetDll": "/absolute/worktree/tools/cosmos-sdk-probe/bin/Release/net10.0/CosmosSdkProbe.dll"
}
```

`sourceSnapshot` is the existing direct-run snapshot shape: `library`, complete
`documents`, and inventory-only `chunks` sorted by `(documentId,id)`. Inventory
fields match `INVENTORY_SQL` in the harness. Original-byte hashes, metadata,
Microsoft profile, pinned embedding revision, ready generation1 and per-source
ownership are checked against the retained manifest. The actual chunk count is
frozen; it need not equal the previous 36. Every document must fit TOP16 for the
bounded exhaustive reference. If that fails, revise the oracle bounds
prospectively with the coordinator, rather than silently truncating.

`ownership` is the new scope ledger from `hybrid_quality_scope`: positive
`createdByThisRun`, `readyCompleted`, library/run IDs, source-to-document mapping,
and protected-library contract; no deletion may have been attempted.
`protectedScopes` contains `libraryIds` exactly matching that contract.
`queryCache` is the retained 48-question embedding cache. Input question hashes
and float16 vector hashes are verified. The sibling `deployment-before.json` and
`deployment-after.json` must retain their archived hashes, model revision and
NoAutoUpgrade properties; there is no embedding fallback.
`healthReceipt` is hash-bound evidence, and `freshHealthAccepted` is the
coordinator's assertion after reviewing that evidence, not a parser inference.

The coordinator supplies a fresh private token JSON with `accessToken` and Unix
`expires_on`, acquired for the target Cosmos audience under the intended existing
identity/subscription. The harness never acquires credentials, changes the Azure
subscription, refreshes the token or reads an application credential file. It
uses the same token bytes in both SDKs. Do not print or commit the token.

```sh
.venv/bin/python -m scripts.hybrid_sdk_comparison prepare \
  --config /absolute/private/client-config.json \
  --output /absolute/private/new-client-state
.venv/bin/python -m scripts.hybrid_sdk_comparison run \
  --state /absolute/private/new-client-state \
  --credential-file /absolute/private/shared-cosmos-token.json
.venv/bin/python -m scripts.hybrid_sdk_comparison summarize \
  --state /absolute/private/new-client-state \
  --output /absolute/private/client-summary-for-review.json
```

`prepare` makes no service calls. The fixed schedule starts only after read-only
live source/container checks. It records all 576 outcomes, including logical
failures, once. It does not selectively retry failed cells. Bounds are 32 pages,
90 seconds per logical query, 12,000 wire attempts per SDK and 1,800 seconds for
the whole run. Exact-reference failure, changed source/container, invalid scope,
credential expiry or guard failure aborts and preserves partial receipts. A
failed run needs diagnosis and a new directory; never overwrite or silently
resume it as the same experiment.

Transport guards allow only HTTPS to the configured account, metadata/point
GETs and query/query-plan POSTs on the configured chunks container. They block
writes and other endpoints. The owner gate binds the logical library query;
internal SDK rewrites are recorded as sent, allowing actual lost partition
bounds to be observed. No library mutation/cleanup or resource provisioning is
implemented. Root performs owned-resource cleanup separately after archiving.

## Reading and accepting results

Review the code and private evidence before committing the allowlisted summary.
All HTTP headers are allowlisted without Authorization/Cookie. Private response
bodies, document text, query parameters, URLs, IDs, activities and diagnostics
remain private. The export contains public source IDs/chunk indexes, hashes,
counts, aggregate charges, timing and rank/coverage metrics; it omits service
identifiers, URLs, credentials and raw text. Privacy review remains required.

Acceptance requires a complete fixed matrix, unchanged source/container
snapshots, matching input/code hashes, exact pinned clients, expected source
isolation and explanation of any failed cell. Check:

- Exact vector recall at8/16 uses all eight full-document brute-force queries,
  descending cosine similarity and cutoff-tie tolerance. It is a ranking oracle
  for these stored rows; small-corpus index engagement remains unproven.
- Hybrid reports query dependence, document coverage and cross-client/route
  agreement. Agreement or many distinct result lists does not establish exact
  hybrid correctness. Exact RRF ranking and passage/fact coverage remain open.
- Physical range headers are positive evidence of contacted ranges. Full-key
  references establish observed document placement. Missing headers mean
  unknown scope. A one-range target cannot prove absence of unnecessary
  large-container fan-out or resolve the earlier two-range R1 cost finding.
- Query-plan counts, query-plan response bodies and SDK diagnostics distinguish
  passthrough, fallback and ranking pipelines. HTTP400/substatus1004 fallback,
  HTTP304 metadata refresh, repeated data requests and429s are separate facts;
  total hidden SDK retries remain unknown.
- Known wire RU sums captured response charges. SDK page RU overlaps that sum
  and is not added. Missing charges and total billed RU stay unknown. Preparation,
  references, warmups and measured queries are reported separately.
- Instrumented logical latency includes capture I/O and SDK runtime differences;
  wire time excludes receipt writes. This is not a production concurrency or
  latency benchmark. Compare matched serial cells, and disclose remaining
  transport/runtime differences.

Do not infer an application routing change from ranking success alone. The next
integration decision must consider both retrieval behavior and actual scope/RU,
and keep subsequent representation/model findings separately attributable.
