# Build plan

*Version 1.9, 2026-10-27: final image-path efficiency and readiness review (E44). Version 1.8, 2026-10-27: image/caption pairs stored during ingestion with read-only retrieval; post-answer caption recovery removed (E43). Version 1.7, 2026-10-26: candidate/answer separation, source-caption association and bounded PDF page-image lookup implemented (E42). Version 1.6, 2026-10-26: text-first publication and deferred image enrichment implemented (E41). Version 1.5, 2026-10-26: PDF page rendering replaced by layout-defined figure crops, body/table text and linked source captions (E40). Version 1.4, 2026-10-26: reproduced Phase 3B review failures fixed with immutable generation plans, retry checkpoints, chart-selection and OCR fallback changes; Azure conditional-write validation passed. Version 1.3, 2026-10-26: Phase 3B images implemented and live smoke-tested, extended to PDF visual pages including vector charts. Version 1.2, 2026-10-26: adaptive document retrieval and prototype performance workbench implemented and smoke-tested; mixed-context comparison added to evidence. Version 1.1, 2026-10-23: Phase 3's step 2 and filters live checks passed. Version 1.0, 2026-10-23: Phase 3's Proposals item built in code. Version 0.9, 2026-10-23: Images move out of Phase 3 into Phase 3B, so metadata and lifecycle can be finished and demonstrated first; Phase 3's Filters item built in code. Version 0.8, 2026-10-23: Phase 3's Lifecycle live check passed (`records/phase3-exit-lifecycle-20261023T210242Z.json`). Version 0.7, 2026-10-23: Phase 3's Lifecycle item built in code (replacement, findings 1, 6 and 7). Version 0.6, 2026-10-23: the independent review's findings and their later-phase items, recorded in `review-response.md`. Version 0.5, 2026-10-22: Phase 2 passed (adapted exit check). Version 0.4: search is vector-only by default; hybrid is a setting, because routed by partition key it does not rank (`evidence.md` E27). Version 0.3: Entra authentication moved from Phase 1 to Phase 5 (the platform authenticates its users; the prototype uses a static local key). Version 0.2: Phase 0 passed; its findings are folded into Phases 0, 4 and the risks.*

The plan is ordered to reach something demonstrable as early as possible:

- Phases 0–2 are the critical path to a working demo.
- Phases 3, 3B and 4 make the demo convincing: filters and proposals, images, and proof that the library ceiling is gone.
- Phase 5 is the road to production.

Each phase ends with an exit check that must pass before the next phase starts.

## Phase 0: Foundations and the canary

**Status: passed on 2026-10-22.** The record is `records/canary-20261022T201837Z.json`, and the findings are in `evidence.md` (E8, E10, E12, E21, E24, E25; N1, N8, N10–N12).

The first build of this exact Cosmos configuration happens here (`evidence.md` N1), so this phase proves the configuration works before any application code depends on it.

**Build**

- Repository scaffold:
  - Python 3.12 or later, one package with two entry points (`api`, `worker`);
  - FastAPI, `azure-cosmos` 4.17.1 or later (originally 4.9; raised in the review response, E27), `azure-identity`, `azure-storage-blob`, `azure-servicebus`;
  - `pytest`, `ruff`.
- Bicep for one organization's deployment:
  - Cosmos account: serverless for the demo, `EnableNoSQLVectorSearch`, `disableLocalAuth`;
  - database `fli` and the three containers, loaded from `cosmos/*.json`;
  - storage account with the `files` container;
  - Service Bus Standard with the session-enabled `ingest` queue;
  - Azure OpenAI deployments: `text-embedding-3-large` at 3,072 dimensions, plus a vision-capable chat model;
  - Document Intelligence and Application Insights;
  - one user-assigned managed identity with the roles in `spec.md` §3, including the Cosmos data-plane role assignment.
- `scripts/canary.py`, run against a freshly deployed account:
  1. Write, read and query a record in each container.
  2. Load about 1,500 chunks of real text, embedded with the real model, into one test library. Use real text: random vectors are invalid test data (`evidence.md` E14). Then wait until a vector search stops scanning: the index engages minutes after writes (E25).
  3. Run 20 vector-only and 20 hybrid searches using the float16 query path (`spec.md` §8 step 3).
  4. Assert:
     - no null scores;
     - the index is engaged: request charge in tens of RU, and the retrieved-document count from the query metrics far below 1,500;
     - recall@10 of at least 0.95 against exact search for vector-only searches (`VectorDistance(…, true, …)` forces brute force). Hybrid searches have no exact reference, because the flag has no effect inside `RRF` (E24) (that explanation was withdrawn in Phase 1: the hybrid searches never ranked, E27); the canary records their agreement with the brute-force variant without asserting it.
  5. Run one search that declares `float32` and record that it scans. This documents the trap.
  6. Write the results to `records/canary-<UTC timestamp>.json`.

**Exit check:** the canary passes on the deployed account and its record is committed.

**If it fails:**
- Recreate the account and rerun. Replacing an account is a normal recovery step (E17).
- If the spherical quantizer or float16 is the cause, fall back to the product quantizer (generally available) and record the change in `spec.md`.

## Phase 1: The core path

**Status: passed on 2026-10-23.** The record is `records/phase1-exit-20261023T013913Z.json`: 20 real documents (PDF, DOCX, CSV, TXT, MD, HTML, EML; 5,535 chunks) reached `ready`, 11 REST searches returned relevant chunks with file names and locations at 10.6–16.5 RU, and a personal library refused another agent with `403`. PPTX and XLSX were left out of the live corpus and are covered by unit tests only. The first run (`records/phase1-exit-20261023T012726Z.json`) found that hybrid search routed by partition key does not rank (`evidence.md` E27); search is vector-only by default since. Entra authentication moved to Phase 5.

**Build**

- **Caller check:** a static key (`AUTH_MODE=dev`) that refuses to start outside `ENVIRONMENT=local`. No Entra in the prototype: the platform authenticates users and decides which libraries they and their agents see (`spec.md` §5). Entra validation is Phase 5.
- **Libraries:** the endpoints in `spec.md` §6.1, with the personal-library rules of §5.
- **Documents:** upload → Blob → queue → worker → `ready`, for PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, HTML and EML. Start from the extraction, OCR, chunking, embedding and query modules (moved from `inherited/` into `src/fli` with their tests, `spec.md` §13). Generations and deterministic chunk ids from the start.
- **Search:** `spec.md` §8 in full. That includes the float16 query conversion, hybrid SQL, filters, the generation check, neighbours and coverage.
- **Tool:** `GET …/tool` (`spec.md` §6.4).

**Unit tests that must exist**

- Every rendered vector or hybrid query has `TOP`, declares `float16`, filters `c.libraryId` by equality on one value, and never selects `c.embedding`. Build these as static checks on the query builder; `src/fli/query.py` `check_query` already does.
- float16 conversion: rounding, and flushing values below 2⁻¹⁴ to 0.
- A personal library searched with another agent's id returns `403`. `PATCH` of `kind` or `boundAgentId` returns `400`.
- Chunk ids are deterministic. A retried ingest overwrites rather than duplicates.
- The generation check drops chunks of replaced and deleted documents.

**Exit check**

- Twenty real documents uploaded to a shared library reach `ready`.
- REST searches return relevant chunks with file names and locations.
- A personal-library search with the wrong agent id is refused.

## Phase 2: The demo platform and its agents

**Status: passed on 2026-10-22**, against an exit check adapted for what Phase 3 still owes. Filters and proposals are Phase 3, so tags, proposals and the period and region filters were left out. The checks were run live by the user in the demo app (`demo/`), against the demo deployment:

- **Corpus:** 34 arXiv papers on retrieval-augmented generation in three shared libraries ("Rag methods" 17, "Retrieval infrastructure" 10, "Evaluation and context" 7), loaded with `scripts/load_demo_corpus.py`, plus four personal notes written for the demo. It is PDF only; the spreadsheet, emails and images of "Demo corpus" below are still to add.
- **Step 2:** all 34 documents reached `ready`; the admin wrote each library's description by hand.
- **Step 3:** an official agent with all three libraries attached, asked "How does Self-RAG decide when to retrieve?", chose the "Rag methods" tool and answered correctly, citing the Self-RAG paper's pages 2–3: 811 ms, 29.87 RU, with neighbours (one extra query per result document).
- **Step 5:** a personal agent with four uploaded notes answered "What chunk size did we decide on, and why?" from the notes alone, citing the right note: 547 ms, 16.67 RU.
- **Step 6:** the attach screen offered only shared libraries; a forced search of the personal library by the official agent returned `403 library_not_bound_to_agent`.

The search response now reports `usage` (request charge and server time, `spec.md` §6.3), so the platform can show cost without reading the API's logs.

The request charges reported for Phases 1 and 2 (10.6–16.5, 29.87 and 16.67 RU) predate the review response (`review-response.md`, finding 3). They exclude the library read and the generation check's document reads, about 1 RU each, which `usage.requestCharge` now includes.

The demo needs something playing the platform's role. Build a small Streamlit app that does exactly what the real platform will do, and nothing the index should do.

**Build**

- **Admin page:** create shared libraries, upload files, watch status, edit description and tags.
- **Agents page:**
  - official agents with any number of shared libraries attached;
  - personal agents, each with its own personal library.
  - The attachment list lives in the demo app, as it will live in the real platform. The index never stores it.
  - The attach screen for official agents lists only shared libraries.
  - A clearly labelled "force attach via API" control demonstrates that the index refuses a personal library anyway.
- **Chat page:**
  - pick an agent;
  - the agent loop gives the chat model one tool per attached library, from `GET …/tool`;
  - show which library tool was called with which filters, the request charge and latency, the citations, and any images.
  - The demo uses an Azure OpenAI chat deployment with function calling. In production the platform's own model does this.

**Exit check:** demo script steps 2, 3, 5 and 6 (below) run end to end.

## Phase 3: Metadata and lifecycle

**Status: passed on 2026-10-23.** Every exit check below ran live: demo step 2, the filters check (`records/phase3-exit-filters-20261024T023848Z.json`) and the lifecycle check (`records/phase3-exit-lifecycle-20261023T210242Z.json`). Images are Phase 3B.

**Build**

- **Proposals (enhancement 1):** per-document tag, period and author proposals, with an accept or edit screen. Per-library description proposals. **Built and unit-tested on 2026-10-23; its live check (demo step 2) passed the same day.** Proposals are made during ingestion and auto-accepted in libraries that allow it. They are accepted, edited or dismissed by document `PATCH` (`acceptProposals`, `dismissProposals`) and on the demo's Admin page, which also accepts or edits the library description proposal (`spec.md` §9.1).
- **Filters:** all filters in search and in the tool definition (`period`, `authoredBetween`, `uploadedBetween`, `authors`, `tags`, `fileTypes`, `contentKinds`). Add the `retag` job. **Built and unit-tested on 2026-10-23; its live check passed the same day** (`records/phase3-exit-filters-20261024T023848Z.json`). Coverage notes count the documents a filter left out for lacking the field, and name filter values no document has; the tool offers only filters that can narrow the search (`spec.md` §6.3, §6.4). Metadata is edited by document `PATCH`, with an edit form in the demo's Admin page; `retag` copies it to the chunks, retrying a stale read (E28). A chunk patch costs about 80 RU, a little over half a write (§7.7, `evidence.md` E32).
- **Room for images:** per-generation image paths in the purge, a working `contentKinds` filter, and an image slot in the demo chat. Building images is Phase 3B. **Done** with Filters.
- **Lifecycle:** document replacement and deletion; library deletion. **Built on 2026-10-23; its live check passed the same day** (`records/phase3-exit-lifecycle-20261023T210242Z.json`). Deletion was built in Phases 1–2 and the review response; replacement (`PUT …/content`) here, with Replace and Delete controls in the demo's Admin page. From the review response (`review-response.md`):
  - Give each generation's original its own blob path, or enable blob versioning (finding 7). **Done:** its own path, `…/original/{generation}/{fileName}`; the purge deletes obsolete generations' originals (`evidence.md` E30).
  - When an ingest is redelivered after its generation already committed, queue the purge again (finding 1). **Done:** any ingest whose generation is no longer pending queues the purge again (`spec.md` §7.1).
  - *Found in planning:* a generation number could be reused after a failed replacement, and the worker could lose a replacement to a stale read. **Done:** a `generation` counter on the record that only increases, and a stale-read retry for ingest (E28, E30).

**Exit check**

- Demo script step 2 runs in full, including the index proposing a description and tags and the admin accepting them. **Passed on 2026-10-23**, run by the user in the demo app against the demo deployment. 30 documents (7 of them uploaded twice by mistake) reached `ready` in about 8 minutes, each with proposals; none failed. A description was proposed after the first 7 documents and again at 30, 8 seconds after the last one was ready. The admin accepted proposals as they were and edited one tag (one document's proposed period is absent from its record, consistent with a dismiss), then accepted an edited description. Every accept reached the chunks, and the tool definition then offered the tag, author and period filters. A filtered question in the Chat page applied `tags` and `fileTypes` and returned only the matching document. Follow-up checks the same evening: authors proposed for 36 files with no author metadata matched the sample corpus's answer key for 34 (the other two named the writer of a transcribed letter and dropped an editor); auto-accept filled tags at ingestion with no `retag`; a replacement kept its accepted metadata. The proposed tag vocabulary drifted, with one key written several ways (`evidence.md` E32).
- A period-plus-tag filtered question returns only matching documents, and the coverage notes say what was excluded. It runs with `scripts/phase3_exit.py filters`, on a manifest of real documents and their metadata, and also times a tag change reaching search and records what `retag`'s patches cost. **Passed on 2026-10-23** (`records/phase3-exit-filters-20261024T023848Z.json`): 8 documents (three public company filings, five from the sample corpus), 6 filtered searches with no result outside its filters, and each expected coverage note present, including for a tag value no document has. Filtered searches cost 4–7 RU against about 15 unfiltered, except the author filter (15 RU). A tag change reached search in 5.8 seconds and left it in 0.2. A chunk patch cost about 80 RU (E32).
- After replacing a document, its old content is never returned.
- When stale chunks of a replaced document fill the fetch window, search still returns `top` results or reports `thin`; decide between fetching again and reporting (finding 6). **Decided: reporting.** Coverage says how many passages were left out (`spec.md` §8 step 6, E30).
- The replacement and underfill checks run with `scripts/phase3_exit.py lifecycle`, on two real documents (E14). **Passed on 2026-10-23** (`records/phase3-exit-lifecycle-20261023T210242Z.json`): the Self-RAG paper was replaced by "Lost in the Middle" under the same file name. The replacement committed after 9 seconds; none of the 68 searches after the commit returned the old generation, and deleting the document, then the library, removed each from search at once. Of the 4 searches made while the replacement was written, one came back with no results and the coverage note: the new generation's chunks, not yet visible, filled the whole fetch window for the question they answer best (finding 6 at its strongest; `evidence.md` E30). That run's write window was short, so only those 4 searches checked that nothing of the new generation showed before the commit.

## Additional enhancement: adaptive document retrieval

**Status: implemented and smoke-tested live on 2026-10-26; both requested retrieval directions passed. Human answer-quality review and broader live validation remain pending.** This is the next user-requested enhancement after Phase 3 and does not waive the existing Phase 3B/4/5 checks.

- Add a library-bound tool bundle with search, paginated document discovery and complete-text reading.
- Let the agent mix complete documents and selected passages in either direction within a single question, revising its retrieval plan without routine user confirmation.
- Discover named files, files selected by author/tags/dates/type, and candidates for vague references identified through semantic search. Disclose unreadable files and incomplete enumeration.
- Preserve versioned pre-chunking extraction, identify legacy indexed representations, enforce explicit context budgets and never silently truncate full reads.
- Test library binding, lifecycle/purge, races, completeness, budgets, both retrieval directions and mixed evidence. See [document-retrieval.md](document-retrieval.md) for the contract and validation boundary.

**Remaining live evaluation:** the four-paper smoke test and both explicit model sequences passed (`records/document-retrieval-20261026T165258Z.json`). A manual same-library comparison then showed a 37.3% smaller final-answer input for mixed evidence, with higher cumulative input tokens, retrieval RU and retrieval time (`records/full-vs-mixed-retrieval-20261026.json`, E37). Still evaluate localized, whole-file, all-by-metadata, vague-reference and mixed-evidence questions with broader real documents and the actual chat deployment. Review routing and citations, estimated/actual input tokens, RU and latency, refusal cases, the documents indexing-policy update, and replacement/deletion behavior. This does not waive Phase 3B/4/5 work or establish general answer quality.

## Additional enhancement: prototype performance workbench

**Status: implemented and tested locally; library ingestion/retrieval counters verified live on 2026-10-26.** Chat exposes actual model usage, provider/wait timing and retrieval RU/latency. Library Performance exposes active inventory, chunk distributions, actual embedding tokens/retries, chunk-write RU and bounded retrieval history by operation/caller. Personal-library metrics use the binding backstop. The live six-page check is `records/library-performance-smoke.json`.

This uses an optional external single-host journal for the prototype, with no extra Cosmos writes per retrieval. It does not complete Phase 4's production Log Analytics routing report or the scale/quality evaluation. See [observability.md](observability.md) for measurement boundaries, missing historical data and configuration. Production telemetry ingestion/retention and multi-instance aggregation remain outstanding.

## Phase 3B: Images

Images were moved out of Phase 3 on 2026-10-23 so that metadata and lifecycle could be finished and demonstrated first. Phase 3 leaves room for them: image paths, the purge rule, the `contentKinds` filter and the chat's image slot are already in place, and the `chunks` container needs no change (it already indexes `contentKind`). Phase 3B can run before or after Phase 4.

**Status: implemented and live smoke-tested on 2026-10-26.** The user expanded this phase to PDFs and vector charts. Standalone uploads, DOCX/PPTX pictures and cropped PDF figures now have vision descriptions embedded in the same semantic space as passages, source context and locations, image chunks, and 15-minute read-only user-delegation links. Complete reads include kept images. The demo supplies native visual inputs when enabled and resolves selected images inline in the answer. Existing files need reprocessing to gain visuals; no bulk re-ingestion was triggered.

**Validation:** `records/phase3b-images-20261026.json` records ingestion of all four formats, fetching signed image links, vague GDP-chart retrieval, deck-chart retrieval, complete reads with images and supporting text, and both real-model scenarios returning inline image references. The chat UI displayed the GDP chart with its source explanation. An isolated live personal-library check additionally passed binding/cross-library refusal, failed replacement preserving the active image, successful text replacement removing images, stale-generation refusal and deletion. All 510 local tests, lint, formatting and the private reference-privacy check passed. This is a small synthetic smoke check, not broad image-retrieval quality or scale proof.

**Limits and platform contract:** see `image-retrieval.md`. New PDF generations contain layout-defined figure crops with linked source captions; legacy full-page generations remain readable. Native Office chart objects and unsupported media are outside picture extraction. Size rules, the per-document cap and skipped counts bound cost and disclose incomplete visual coverage.

**Architecture review follow-up:** the subsequent [review](consults/2026-10-26-phase3b-architecture-review.md) reproduced an overlapping-ingestion publication race, repeated vision work on retries, chart-selection gaps and an OCR/visual fallback gap. Items 1–4 are now fixed and covered by 523 passing tests, a real Cosmos/Blob overlap and resume check (`records/phase3b-hardening-conditional-20261026.json`), and a fresh four-format/model smoke run (`records/phase3b-hardening-smoke-20261026.json`). PDF figure/source-caption anchoring is implemented (E40); selective visual materialization and broad scale/recall evaluation remain open. The smoke check is not production-readiness evidence. The Cosmos container/partition layout remains appropriate. Controlled reproduction results: `records/phase3b-architecture-review-20261026.json`.

**Final integration review (2026-10-27):** captions and source figures are stored together during ingestion, text publishes before image descriptions, and selected-page PDF reads perform no analysis or writes (E41–E43). Enrichment now loads one stored crop at a time and reuses its reference. Obsolete renderer dependencies are removed, mixed text/image answer guidance is explicit, and coverage disclosures include legacy omissions and Office direct-read limits. All 581 local tests passed. See the [readiness review](consults/2026-10-27-image-readiness-review.md) and E44 for checks and remaining limits.

**Exit check:** demo script step 4 works.

**Issue #2 follow-up:** compact model evidence, parent-file size hints, optional bounded document grouping, interleaved enriched sections, off-loop read tokenization, bounded per-document vision concurrency and provider-usage context accounting are implemented. See [the decision record](consults/2026-10-27-retrieval-followups.md). Earlier E44 serial loading is superseded by a configured memory bound, while the shared HTTP quota and text-first publication remain. Broad recall evaluation stays in Phases 4/5; API response-view changes are deferred.

## Phase 4: Scale proof and routing telemetry

**Build**

- `scripts/scale_demo.py`:
  - Create 1,000 personal libraries, five times AI Search's 200-index limit on S2 and S3, each bound to a synthetic agent and holding 1–5 small real documents (30–150 chunks).
  - Create three large shared libraries of 10,000+ chunks.
  - Measure per-search request charge and latency on a sample of small and large libraries, before and after loading the 1,000.
  - Write a one-page report.
  - At about 100,000 chunks (about 38 million tokens at Phase 0's 381 tokens per chunk), expect:
    - about $3.60 of Cosmos writes on serverless, at about 144 RU per chunk (E10);
    - about an hour of writing, at about 27 chunks per second with two writers;
    - embedding as the real bottleneck: over 4 hours at the default 150,000 tokens per minute. Raise `embeddingCapacity` in `infra/main.bicep`, within the subscription's quota, before loading. At 1,000,000 tokens per minute it takes about 40 minutes.
  - Record the vector index type Cosmos reports (`x-ms-cosmos-index-utilization`) for each library size. Phase 0 saw `QuantizedFlat` at 1,500 chunks. The large libraries should show whether, and from what size, Cosmos walks the DiskANN graph (N11).
  - Measure how long a newly created library takes to stop scanning in a container that is already populated (N12).
  - On a realistic large library with about 2–5% image rows, measure `contentKinds: ["image"]` recall@k against vector-only exact search (`VectorDistance(..., true, ...)`), plus RU, latency, result-slot duplication and the observed index type (N11, N16). Keep `filterPriority` at its default unless those measurements justify a change.
  - Wait for the index before measuring search cost (E25).
  - Measure the cross-partition library list at 1,000+ libraries, and `refresh_library` (which reads every document record after each ingest) during a bulk load into one library (review finding 4).
  - Measure hybrid search cost (`SEARCH_MODE=hybrid`) on the same libraries before and after loading the 1,000: its full-text statistics span the container, so it may grow with the number of libraries (E27).
- **Routing telemetry (enhancement 3):** `library_search` and `search_usage` events, the usage endpoint, the saved Log Analytics query, and `GET …/routing-report`. Show it in the demo app.

**Exit check**

- The report shows more than 200 libraries in one container.
- Per-search cost on a given library is the same, within run-to-run variation, before and after the 1,000 were added.
- The report states the vector index type Cosmos used on small and large libraries.

## Phase 5: Toward production

These come after the demo, in roughly this order:

1. **Infrastructure:**
   - Provisioned autoscale. Convert the demo account (one-way) or deploy a fresh production account.
   - Deploy to Container Apps.
   - App registration and app role for the real platform, and Entra bearer validation (`Platform.Caller`) in place of the static key (`spec.md` §5 rule 1).
2. **Platform integration:**
   - Replace the Streamlit stand-in with calls from the real platform: library lifecycle, uploads, tool attachment, search, usage reports.
   - Run the personal-agent lifecycle end to end: create a personal agent, create its library, then delete both.
3. **Quality:**
   - An evaluation set of 50–100 real questions with known answers over a realistic corpus, including localized, whole-file, all-by-metadata, vague-reference and mixed routing tasks, plus vague, caption-only and topic-only figure questions. Measure image and text recall separately and citation/answer quality, tool rounds, actual input tokens, RU and latency.
   - Gate document-summary vectors on a demonstrated document-selection recall gap after bounded document grouping. Account for ingestion summary cost, text-first publication, the published-count fence, retagging, legacy reads and reprocessing before adding a row type.
   - Evaluate current image embedding text against description/caption/heading plus the nearest paragraph (about 300 characters), with a source-section anchor for explanation recovery. Keep full source context in the vision prompt. Do not change the default or reprocess until image recall improves without losing topic-only figure recall or ordinary text recall. Use vector-only exact references and report slot duplication; record wins and losses.
   - Tune `top`, the over-fetch factor and the RRF weights against it.
   - Measure hybrid against vector-only (`evidence.md` N3). Hybrid's full-text terms include common words; add a stop list before evaluating it (E27).
4. **Open measurements:**
   - Library-only versus class-plus-library sharding on large libraries (N2).
   - A multi-partition container test (N4).
   - Microsoft's confirmation for vector search on containers with hierarchical partition keys (N6).
   - Visibility across API instances: a delete on one instance, then a search on another; decide between passing session tokens and accepting a bounded window (N13).
5. **Coverage:** broader figure/caption accuracy evaluation, email attachments as child documents, conversion of legacy Office formats.
6. **Operations:**
   - Dead-letter alerting, including the worker's `purge dead-lettered` log (E28).
   - A job that verifies purges actually happened.
   - A backup and restore drill covering Cosmos and Blob together (E16).
   - A rebuild-from-originals script for future embedding-model changes (E16).
7. **From the review response** (`review-response.md`):
   - Pagination with continuation tokens on the library and document lists, and library stats updated incrementally rather than recomputed from every record (finding 4).
   - Embed and write in bounded batches instead of holding a whole document's vectors, and size the worker's memory (finding 2, E29).
   - Decide whether Cosmos and Storage keep public network access or move to private endpoints.

## The demo script

About ten minutes. Each step names the phase that delivers it.

1. **The ceiling is gone** (Phase 4). "Azure AI Search allows 200 indexes per service on S2 and S3, and we use one per library. Here are 1,000 libraries in one Cosmos container, and a search costs the same with 10 libraries or 1,000."
2. **An admin builds a library** (Phase 2, proposals in Phase 3).
   - Create "Finance — Quarterly Reports" and upload a dozen PDFs, decks and spreadsheets.
   - Watch them reach `ready`.
   - The index proposes a description and tags; the admin accepts them.
3. **An official agent uses it** (Phase 2).
   - The "Finance Assistant" agent has three shared libraries attached.
   - Ask: "What drove the change in gross margin in EMEA in Q4 2025?"
   - Show that the agent chose the finance library's tool, applied the period and region filters, and answered with citations (file and slide). Show the search's request charge and latency.
4. **Images** (Phase 3B). Ask: "Show me the regional margin chart." The answer includes the chart from the deck, shown in the chat.
5. **A personal agent** (Phase 2). A user builds a personal agent, uploads their own notes, and asks a question only those notes can answer.
6. **Personal stays personal** (Phase 2).
   - The admin's attach screen does not offer the personal library.
   - Forced through the API, the index refuses it.
7. **Which libraries earn their keep** (Phase 4). The routing report: searches, empty results and cited results per library.

## Demo corpus

Use 30–60 real documents in one believable domain:

- PDFs and decks with charts;
- at least one spreadsheet;
- a few emails (`.eml`);
- standalone images;
- 3–5 personal notes for the personal agent.

Use material you are allowed to use, such as public company reports, public statistics publications, or documents written for the demo. Do not use placeholder or generated filler text: it makes search quality meaningless (E14) and the demo unconvincing.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| The spherical quantizer or float16 fails to create, or fails the canary | Recreate the account. Use the product quantizer and float32, record the change, and keep going |
| Hybrid ranking is worse than vector-only on the evaluation set, or costs too much | Already the case for cost: vector-only is the default (`SEARCH_MODE`, E27). Hybrid stays a setting until the evaluation set and Phase 4's container-size measurement justify it |
| Throttling during demo-day uploads on serverless | Load the demo corpus beforehand, or run the demo on a provisioned account |
| A library searched straight after upload scans until the index engages, about 6 minutes in Phase 0 (E25) | Results stay correct; only cost rises. Load demo libraries well before the demo. Measure the lag in a populated container in Phase 4 |
| DiskANN does not engage on large libraries, or costs more than expected (N11) | Phase 4 measures it. `QuantizedFlat` at 15 RU on a 1,500-chunk library is already acceptable for small libraries |
| float16 turns out to be unsupported (N10) | float32 in a new container, rebuilt from stored text (E16). Phase 0 measured float16 working |
| Azure OpenAI quota (embedding tokens per minute) slows bulk loads | Batch, back off on 429, request more quota |
| Image description cost | The skip rules and per-document cap in `spec.md` §7.5 |

## Proposed follow-up after natural agent tests (2026-10-27)

The [agent retrieval strategy](consults/2026-10-27-agent-retrieval-strategy.md) is proposed for user review, not implemented. It separates semantic file finding from evidence search, adds known-document/bounded-source/selected-image capabilities, and improves reference-adapter progress, follow-up evidence and answer-grounding behavior. It preserves whole-file completeness, library isolation and existing Cosmos/vector settings. Phases 4/5 retain their scale/recall obligations.

The [day's test review](consults/2026-10-27-agent-retrieval-test-review.md) links the implementation smoke checks, both eight-question manual suites, all three casual cases and the limits on conclusions. E46–E48 show why passing mechanism tests did not finish natural tool-use integration. Future implementation should meet the proposed answer-quality gates before optimizing latency or token/RU cost.
