# Client and answering-model follow-up: prospective protocol

This new exploratory experiment starts at completed comparison commit
`8f7d436987724abac1efe633d071db66f8cbf4ea`. Preserve that run, its controls and
private archives. No main-branch integration, application deployment, application
default or ingestion-representation change is part of this experiment.

The completed pilot showed better initial retrieval for hybrid, with no established
final-answer advantage under Sol and a 200k allowance. This follow-up asks whether
client behavior explains the routing/ranking distinction, and whether a less costly
answering model benefits differently from the same retrieval choices.

## Independent comparisons

The client comparison pins Python `azure-cosmos==4.17.1` and .NET
`Microsoft.Azure.Cosmos==3.63.0`. Both read identical stored rows and cached question
vectors with matching SQL semantics, library filters, candidate limits and gateway
transport. Each client runs vector with an explicit library partition prefix,
hybrid with that prefix, and hybrid with WHERE-only routing. The proposed schedule
has 48 questions, six cells and two counterbalanced repetitions: 576 measured
queries, plus six separately recorded warmups. Separately counted per-document
exact-vector references can test vector ordering; hybrid query dependence, source
relevance and route agreement must not be mislabeled an exact RRF oracle.

Report ranking separately from actual physical partition scope, RU, latency,
retries and query-plan behavior. An explicit routing header does not by itself
prove physical confinement. A one-range container cannot establish avoided
multi-range fan-out. David Ames's SDK version is known; his exact SQL, options and
partition layout are not. This tests a controlled SDK difference, not a complete
replication of his setup. No scaling resource is used to fill that gap.

The answering pilot uses the unchanged Python retrieval client and unchanged
Microsoft layout representation in four cells: Sol/vector, Sol/hybrid,
GPT-5.4 mini/vector and GPT-5.4 mini/hybrid. It reuses the 48 consumed questions,
once per cell, for 192 exploratory turns. A balanced four-cell schedule gives each
cell each position equally often. Every turn starts a fresh conversation.

Sol is pinned to `gpt-6.1-sol` revision `2026-09-29`; the economical candidate is
`gpt-5.4-mini` revision `2026-03-17`. Fresh actual Azure offerings list Responses
support and sufficient quota for both. Before answering, exact owned deployments
must pass version/SKU/NoAutoUpgrade checks and a separate live function-tool smoke.
Both use GlobalStandard capacity 200. Existing deployments remain protected.

All four cells use a **64,000-token conversation allowance**, medium reasoning,
8,000 maximum output tokens, the existing twelve tool rounds, at most thirteen
logical model calls per turn, three attempts, Responses, storage disabled and no
automatic truncation. The 64k allowance is a chosen common operating budget, not
either model's maximum capacity. A fresh Sol reference is mandatory: the previous
200k results are historical context, not a matched control for this pilot.

The model pilot stops as a whole when any declared limit is reached: 192 turns,
2,496 logical calls, 7,488 physical attempts, 32 million conservative input-token
guard units or two million output-token guard units. Before each attempt, reserve
64k input and 8k output; successful measured usage replaces that reservation,
while unknown/error attempts retain it. These are execution bounds, not an invoice
cap. The separate support smoke permits four logical calls and twelve attempts.
Do not selectively rerun failed cells or raise limits after observing outcomes.

Questions, tools, instructions, embeddings, stored source rows, metadata and
representation remain identical across model cells. Preserve actual adaptive
queries and tool choices; they are outcomes. Report completion, support, citations,
abstention, complete original-region fidelity, requested-fact sufficiency, recovery
searches/reads, refusals, provider tokens, known RU and latency. Separate measured
rate-limit waits; elapsed minus those waits is still not pure useful inference
because provider/network/tool work and failed-attempt overhead remain mixed.

## Representation and future confirmation

The improved selectors are present, but the Microsoft profile stores one canonical
section per original document. It does not exercise the full custom authored
hierarchy. Selecting its only section may read the entire extracted document.
The parallel offline investigation must identify where the known table/list
qualifier structure is lost, including distinctions between original OOXML,
provider paragraph annotations, flattened content and published sections.

Propose the narrow structure-preservation change separately. Do not use it in the
client or model pilot. The larger study proposal should refine roughly 30–50
varied documents and 150–200 fresh questions around source/task coverage, review
work and measured pilot usage. It must name its representation and demonstrate
paragraph/section/chapter navigation, not infer that capability from selector
handles alone. The larger study is not authorized to launch in this task.

## Resource and evidence boundary

Reuse retained isolated validation infrastructure after current health, mounted
workflow and model checks. Reconstruct one owned library from the eight retained
originals with the unchanged Microsoft profile; the previous successful library
and temporary model were deleted. Protect all prior libraries, the older fenced
failed scope and existing deployments. No new Cosmos account/container, worker,
queue, application deployment or scale fixture is planned.

The coordinator owns all cloud mutations and serial measurement windows. Source
preparation, client measurements and answering runs do not overlap. Local coding
and offline review can run concurrently on separate branches/private directories.
Capture actual source/state/model bindings before and after each measurement.
Retain every attempt and unknown counter, then independently review source-based
judgments and code before accepting results. Archive private sources/answers/
provider receipts outside Git before exact-owned cleanup, verify late absence,
and recheck protected resources. Preserve normal retention and prior evidence.

Local setup remains `sh scripts/dev setup`; test/lint actions are in `scripts/dev`.
It contains no cloud provisioning or credentials. Raw content, source/gold text,
resource identifiers, credentials and bearer tokens belong only in private runtime
and archive storage. Public receipts contain reviewed counters, public case labels,
control hashes and explicit limitations.

Model capability references checked 2026-11-04:
[Azure model specifications](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure),
[GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
Official API list prices may inform labeled estimates; actual Azure rates,
caching, retry charges and complete bills require separate evidence.
