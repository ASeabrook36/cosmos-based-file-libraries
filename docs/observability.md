# Prototype observability

The prototype exposes measurements for Azure architects and AI developers at two levels: a performance summary on each chat answer, and a per-library inventory/history dashboard. These are measurements of the observed calls, not a claim about production-scale throughput, answer quality or the Azure invoice.

## Per-answer performance

Chat shows:

- Cosmos RU reported by the retrieval API calls in the turn;
- elapsed retrieval time across those calls;
- total response time, including tool setup, model calls, token accounting and rate-limit waits;
- actual model input, completion and total tokens, cumulative across calls;
- provider-request time, rate-limit wait time and retry count per model call;
- cached input and reasoning tokens when the provider supplies them.

Cached input is a subset of input tokens; reasoning is a subset of completion tokens. Neither is added again to the totals. Retained evidence can contribute to the input usage of several calls. Provider request time includes client/network/authentication work and is not a measurement of model generation alone. An unavailable counter is shown as unavailable, not zero.

The expandable breakdown lists individual retrieval and model calls. Retrieval time includes embedding, API, Cosmos, Blob and network work. Reported retrieval RU excludes UI/library metadata reads and tool-definition reads, which are separate Cosmos operations. Failed calls may have unreported charge, so totals are labelled as a lower bound when appropriate. Complete-file size estimates remain separate from actual model usage.

`DEMO_METRICS_FILE`, when set to a path outside the repository, writes content-free per-turn JSONL: numeric usage/timing fields, operation labels, completeness and error flags. It does not write questions, answers, source text, filenames, library/agent identifiers, filter values, endpoints or credentials. The UI retains the answer and its metrics in session memory as before.

## Per-library performance

**Library Performance** is a new demo page, also linked from Admin. Its initial view lists shared libraries. The underlying `GET /v1/libraries/{libraryId}/metrics` endpoint requires the platform credential and `X-Agent-Id`, applies the personal-library binding backstop, and rechecks availability after collecting data. The platform remains responsible for which shared libraries and dashboards a caller may see.

### Inventory, chunks and embeddings

The inventory describes current active generations: documents, chunks, image count, embedding model and dimensions. A per-document table shows status, generation, counts, extraction-size estimates and captured processing measurements:

- extraction and chunking elapsed time;
- total/minimum/maximum/mean chunk token counts, tokenizer and chunking version;
- actual embedding input tokens, request count, throttles, retries and embedding elapsed time;
- chunk-write elapsed time and Cosmos request charge;
- worker processing time up to the generation commit.

`processingMetrics` is committed with the active document generation, so a pending/failed replacement retains the old active measurements. Measurements begin with ingestion under the instrumented worker. Old files do not acquire invented historical token counts or charges. Reprocessing an original captures a new generation's measurements.

Embedding accounting uses a per-call context, not subtraction of a shared client's cumulative counters. Concurrent libraries and queries therefore do not inherit one another's token usage. The provider's actual `usage.prompt_tokens` is used; token-count estimates are not presented as billed embedding usage.

Chunk-write RU meters responses from chunk creates (legacy/import upserts) and collision verification reads, including responses observed during their retries. It excludes document/library metadata, Blob, queue and proposal operations, and earlier failed processing attempts. Worker processing time excludes queue wait and post-commit refresh/purge work. These are explicit boundaries, not a whole-ingestion bill.

### Retrieval history

With `FLI_METRICS_FILE` configured outside the repository and shared by the local API and worker, the prototype appends allowlisted, content-free events. It adds no Cosmos writes per retrieval. The dashboard shows:

- search, complete-read and catalogue calls;
- RU, latency, result counts, dispositions, complete reads and budget refusals;
- calling-agent identifiers in the protected runtime view;
- error status and operation, without exception bodies;
- query-embedding input tokens and time when captured;
- median/p95 latency and charge by operation, plus the individual event log.

The reader scans at most the last 4 MiB of the journal and retains at most 2,000 matching events. The dashboard reports when that window is bounded and when observation began. Counts and percentiles cover that sample only, not lifetime use. A p95 based on a tiny sample is not a capacity benchmark. Failures with unknown RU make aggregate charge a lower bound. Older calls made before collection was enabled are not reconstructed.

The local journal is a **single-host prototype adapter**, not a distributed analytics store or an audit log. It is best-effort: an unavailable sink does not fail the retrieval. Retention/rotation and multi-instance aggregation belong to the telemetry backend in production. Export the same fields to the organization's monitoring system rather than sharing this local file across hosts. The supplied Application Insights/Log Analytics resources do not automatically receive this local journal; their production ingestion/query integration remains to be built.

Model response tokens belong to the whole agent turn, potentially involving several libraries. The prototype does not fabricate per-library allocations of those tokens. Chat provides that layer of measurement; the library dashboard reports its own retrieval and embedding work.

## Configuration and privacy

All environment-specific values remain outside the repository:

| Setting | Purpose |
|---|---|
| `DEMO_CONTEXT_TOKENS` | Initial usable context budget in Chat; default 64,000, to be set within model/deployment limits |
| `DEMO_METRICS_FILE` | Optional external per-answer metrics JSONL |
| `FLI_METRICS_FILE` | Optional external shared API/worker library-event journal |

Both file sinks refuse locations inside the reference repository. Library events require runtime IDs for isolation/correlation; keep that journal private and external. Repository evidence uses aggregate metrics and public source role labels/hashes, without environment identifiers. Run `scripts/check_reference_privacy.py` with a private deny file outside the repository before publishing evidence.

## Validation

`tests/test_demo_telemetry.py` checks token normalization, cumulative usage, timing transport and content-free output. `tests/test_library_metrics.py` checks library isolation, personal-library refusal, bounded history, malformed lines, missing metrics, real API event recording and concurrent embedding attribution. The existing lifecycle and query tests still apply.

A live six-page reprocessing smoke check is recorded in [library-performance-smoke.json](../records/library-performance-smoke.json): 18 chunks, 6,584 actual embedding input tokens over two requests, 2,592.65 RU for chunk writes, and no embedding retries. Three subsequent retrieval calls were measured by the journal. These are single-run observations with the exclusions above. The UI was inspected against the live API; broad performance and answer-quality evaluation remain separate work.

## Retrieval-first acceptance criteria

For this reference architecture, prioritize correct library/document selection, metadata/name filters, active-generation visibility, complete versus partial coverage, and the clarity of tool descriptions and result metadata. Verify both complete-file → passage-search and passage-search → complete-file composition. Measure RU and API/client retrieval latency by operation, separately from model calls and provider waits.

The manual mixed-retrieval smoke check reported 34.30 retrieval RU, about 1.98 seconds across three retrieval API calls, and about 37.75 seconds end-to-end. Approximately 35.60 seconds were in four model requests, with no recorded quota wait. These are observations of that run, not evidence of storage latency or production-model speed. A different model or orchestration strategy may change model time; increasing rate allowance primarily addresses throttling and does not establish faster generation.

Correct tool execution does not validate every conclusion the model draws from the files. The manual answer also made an unsupported leap from long-context position bias to recommending whole-file reads as a cure. Agent guidance now distinguishes coverage/synthesis needs from attention limits and distinguishes paper-specific trained mechanisms from capabilities actually provided by the platform. Human answer/citation review remains separate from retrieval acceptance. The source findings are in [Lost in the Middle](https://arxiv.org/abs/2307.03172) and [Self-RAG](https://arxiv.org/abs/2310.11511).

## Whole files versus mixed evidence

Two fresh chats used the same library and agent instructions, with different question wording. The first broad comparison request led to two complete-document reads. The second explicitly requested the whole primary document and relevant passages from the reference document; the agent read one complete file and then searched for eight passages. The content-free record is [full-vs-mixed-retrieval-20261026.json](../records/full-vs-mixed-retrieval-20261026.json).

| Observed measure | Both files complete | Whole primary + reference passages |
|---|---:|---:|
| Model calls | 4 | 5 |
| Input to final answer call | 17,670 tokens | 11,078 tokens |
| Cumulative model input | 25,161 tokens | 26,223 tokens |
| Cumulative model output | 4,866 tokens | 5,752 tokens |
| Reported retrieval RU | 17.61 | 29.01 |
| Retrieval elapsed time | 0.96 s | 2.05 s |
| End-to-end time | 52.27 s | 51.82 s |
| Recorded quota wait | 0 s | 0 s |

The mixed workflow reduced input to the final answer call by **37.3%**. It demonstrates that an agent can keep complete coverage of one source while limiting another source to relevant passages. That is a concrete reason to offer passage retrieval alongside complete reads.

It did **not** reduce cumulative tokens, RU or retrieval time in this pair. The extra model/tool round processed retained context again, and semantic search performed embedding/query work while the comparison complete-read path fetched an already stored extraction. Smaller final context is a context-budget observation, not a measurement of improved attention or accuracy. Different prompts, answer lengths and cache behavior, with one run per condition, prevent a controlled cost or latency claim. Human inspection found the mixed answer useful and cited, but independent citation/accuracy scoring was not performed.

This supports the tool contract and explicit mixed composition. It does not make the retrieval service responsible for perfect agent scope selection; see the [responsibility boundary](document-retrieval.md#responsibility-boundary).


The `images` retrieval operation meters reads of ingested source image/caption assets. It performs no layout analysis or answering-time caption recovery. The E42 caption-revision timings describe the superseded prototype path; new turns do not generate an extra answer to incorporate a caption. `sourceImageCount`/`sourceImagesSkipped` describe stored source coverage; `imageCount`/`imagesSkipped` describe semantic image indexing.
