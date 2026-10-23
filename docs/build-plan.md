# Build plan

*Version 0.5, 2026-10-22: Phase 2 passed (adapted exit check). Version 0.4: search is vector-only by default; hybrid is a setting, because routed by partition key it does not rank (`evidence.md` E27). Version 0.3: Entra authentication moved from Phase 1 to Phase 5 (the platform authenticates its users; the prototype uses a static local key). Version 0.2: Phase 0 passed; its findings are folded into Phases 0, 4 and the risks.*

The plan is ordered to reach something demonstrable as early as possible:

- Phases 0–2 are the critical path to a working demo.
- Phases 3–4 make the demo convincing: images, filters, and proof that the library ceiling is gone.
- Phase 5 is the road to production.

Each phase ends with an exit check that must pass before the next phase starts.

## Phase 0: Foundations and the canary

**Status: passed on 2026-10-22.** The record is `records/canary-20261022T201837Z.json`, and the findings are in `evidence.md` (E8, E10, E12, E21, E24, E25; N1, N8, N10–N12).

The first build of this exact Cosmos configuration happens here (`evidence.md` N1), so this phase proves the configuration works before any application code depends on it.

**Build**

- Repository scaffold:
  - Python 3.12, one package with two entry points (`api`, `worker`);
  - FastAPI, `azure-cosmos` 4.9 or later, `azure-identity`, `azure-storage-blob`, `azure-servicebus`;
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

## Phase 3: Images and metadata

**Build**

- **Images:** standalone uploads and images embedded in DOCX and PPTX; skip rules and cap; vision descriptions; image chunks; short-lived read links. Render images in the chat, and pass them into the model's context when it accepts images.
- **Proposals (enhancement 1):** per-document tag, period and author proposals, with an accept or edit screen. Per-library description proposals.
- **Filters:** all filters in search and in the tool definition (`period`, `authoredBetween`, `uploadedBetween`, `authors`, `tags`, `fileTypes`, `contentKinds`). Add the `retag` job.
- **Lifecycle:** document replacement and deletion; library deletion.

**Exit check**

- Demo script step 4 works.
- A period-plus-tag filtered question returns only matching documents, and the coverage notes say what was excluded.
- After replacing a document, its old content is never returned.

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
  - Wait for the index before measuring search cost (E25).
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
   - An evaluation set of 50–100 real questions with known answers over a realistic corpus.
   - Tune `top`, the over-fetch factor and the RRF weights against it.
   - Measure hybrid against vector-only (`evidence.md` N3). Hybrid's full-text terms include common words; add a stop list before evaluating it (E27).
4. **Open measurements:**
   - Library-only versus class-plus-library sharding on large libraries (N2).
   - A multi-partition container test (N4).
   - Microsoft's confirmation for vector search on containers with hierarchical partition keys (N6).
5. **Coverage:** PDF figure extraction (Document Intelligence layout), email attachments as child documents, conversion of legacy Office formats.
6. **Operations:**
   - Dead-letter alerting.
   - A job that verifies purges actually happened.
   - A backup and restore drill.
   - A rebuild-from-originals script for future embedding-model changes (E16).

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
4. **Images** (Phase 3). Ask: "Show me the regional margin chart." The answer includes the chart from the deck, shown in the chat.
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
